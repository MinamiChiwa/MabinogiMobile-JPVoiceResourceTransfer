"""Portable, content-checked AudioClip locations for faster offline transfers."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from resource_progress import emit_progress

FORMAT = 1


def fingerprint(root: Path) -> dict[str, Any]:
    from transfer_jp_voice_resources import iter_blob_files, sha256_file

    manifests = [
        {"path": p.name, "sha256": sha256_file(p)}
        for p in sorted(root.iterdir())
        if p.is_file() and "obd-manifest" in p.name.lower()
    ]
    inventory = [
        {
            "path": p.relative_to(root).as_posix(),
            "bytes": p.stat().st_size,
            # A launcher update can replace a blob with another file of the
            # same length. Content identity prevents stale index reuse.
            "sha256": sha256_file(p),
        }
        for p in iter_blob_files(root)
    ]
    if not inventory:
        raise ValueError(f"No package files below {root}")
    return {"manifests": manifests, "inventory": inventory}


def index_blob(task: tuple[str, str]) -> dict[str, Any]:
    """Workers read only; no Unity environments or decoded audio are returned."""
    import UnityPy
    from transfer_jp_voice_resources import (
        clip_signature, is_voice_clip, iter_unityfs_slices,
        normalize_container, resource_basename, sha256_bytes,
    )

    root, relative = task
    data = (Path(root) / relative).read_bytes()
    clips: list[dict[str, Any]] = []
    errors: list[str] = []
    engines: set[str] = set()
    bundles = 0
    for offset, bundle in iter_unityfs_slices(data):
        try:
            env = UnityPy.load(bundle)
        except Exception as exc:
            errors.append(f"{relative}@{offset}: {exc}")
            continue
        bundles += 1
        engine = str(getattr(env.file, "version_engine", ""))
        if engine:
            engines.add(engine)
        for obj in env.objects:
            if obj.type.name != "AudioClip":
                continue
            try:
                clip = obj.read()
                name = str(getattr(clip, "m_Name", "") or "")
                container = normalize_container(obj.container)
                if not name or not is_voice_clip(name, container):
                    continue
                res = getattr(clip, "m_Resource", None)
                clips.append({
                    "name": name, "container": container,
                    "signature": clip_signature(clip),
                    "bundle_offset": offset, "path_id": int(obj.path_id),
                    "length": float(getattr(clip, "m_Length", 0.0) or 0.0),
                    "resource_name": resource_basename(getattr(res, "m_Source", "")),
                    "resource_offset": int(getattr(res, "m_Offset", 0) or 0),
                    "resource_size": int(getattr(res, "m_Size", 0) or 0),
                })
            except Exception as exc:
                errors.append(f"{relative}@{offset}:{obj.path_id}: {exc}")
    return {
        "path": relative, "bytes": len(data), "sha256": sha256_bytes(data),
        "bundles": bundles, "engine_versions": sorted(engines),
        "clips": clips, "errors": errors,
    }


def write_index(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def get_index(root: Path, package_dir: Path, role: str, workers: int, progress: bool = True) -> dict[str, Any]:
    stage = "scan_" + role.lower()
    emit_progress(stage, operation="fingerprint", enabled=progress)
    identity = fingerprint(root)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    local = package_dir / ".voice-index-cache" / f"{role}-{digest}.json"
    for candidate in (local, package_dir / f"{role}_voice_index.json"):
        if not candidate.is_file():
            continue
        try:
            index = json.loads(candidate.read_text(encoding="utf-8"))
            if index.get("format") == FORMAT and index.get("fingerprint") == identity:
                if progress:
                    print(f"{role.upper()}: using voice index ({index['clip_count']} voice objects); no full Unity scan.", flush=True)
                emit_progress(stage, len(index["files"]), len(index["files"]), unit="files", complete=True, operation="cache", enabled=progress)
                return index
        except (OSError, ValueError, KeyError):
            pass
    tasks = [(str(root), item["path"]) for item in identity["inventory"]]
    if progress:
        print(f"{role.upper()}: first scan, {len(tasks)} package files, {workers} workers. This reads only; index is reused next time.", flush=True)
    records: list[dict[str, Any]] = []
    emit_progress(stage, 0, len(tasks), unit="files", enabled=progress)
    if workers == 1:
        iterator = map(index_blob, tasks)
        for i, record in enumerate(iterator, 1):
            records.append(record)
            emit_progress(stage, i, len(tasks), unit="files", enabled=progress)
            if progress and (i == 1 or i % 25 == 0 or i == len(tasks)):
                print(f"{role.upper()} scan files {i}/{len(tasks)} (not replacement count)", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, record in enumerate(pool.map(index_blob, tasks), 1):
                records.append(record)
                emit_progress(stage, i, len(tasks), unit="files", enabled=progress)
                if progress and (i == 1 or i % 25 == 0 or i == len(tasks)):
                    print(f"{role.upper()} scan files {i}/{len(tasks)} (not replacement count)", flush=True)
    if fingerprint(root) != identity:
        raise RuntimeError("Client files changed during indexing. Close the game/updater and retry.")
    index = {
        "format": FORMAT, "fingerprint": identity, "files": records,
        "clip_count": sum(len(x["clips"]) for x in records),
        "engine_versions": sorted({v for x in records for v in x["engine_versions"]}),
    }
    errors = [error for record in records for error in record["errors"]]
    if errors:
        raise ValueError(f"Index incomplete ({len(errors)} read errors); first error: {errors[0]}")
    try:
        write_index(local, index)
    except OSError as exc:
        print(f"WARN index could not be saved: {exc}", flush=True)
    emit_progress(stage, len(tasks), len(tasks), unit="files", complete=True, enabled=progress)
    return index


def verify_files(root: Path, index: dict[str, Any], paths: set[str]) -> None:
    from transfer_jp_voice_resources import sha256_file

    records = {x["path"]: x for x in index["files"]}
    for relative in sorted(paths):
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            raise ValueError(f"Invalid path in voice index: {relative}")
        record = records.get(relative)
        if record is None or sha256_file(path) != record["sha256"]:
            raise ValueError(f"Voice index no longer matches {relative}; use a clean source client or rebuild the index.")
