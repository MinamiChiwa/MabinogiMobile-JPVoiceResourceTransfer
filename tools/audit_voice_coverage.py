"""Read-only coverage audit for indexed TW voices and a bundled JP cache.

The audit compares compressed resource payload hashes from a pristine TW tree,
the installed/output tree, and the selected JP cache. It does not write either
client tree; only the requested JSON report is created.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import UnityPy

from transfer_jp_voice_resources import clip_signature, iter_unityfs_slices, normalize_container, resource_basename


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_records(package: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    metadata = json.loads((package / "resource_cache.json").read_text(encoding="utf-8"))
    raw = (package / "resource_cache.bin").read_bytes()
    if metadata.get("format") != 1 or metadata.get("source") != "MabinogiMobile_JP":
        raise ValueError("unsupported resource cache format/source")
    if metadata.get("clip_count") != len(metadata.get("clips", [])) or metadata.get("payload_bytes") != len(raw):
        raise ValueError("cache resource checksum count/size mismatch")
    by_full: dict[tuple[str, str], dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for item in metadata.get("clips", []):
        offset, size = int(item["offset"]), int(item["size"])
        if offset < 0 or size <= 0 or offset + size > len(raw):
            raise ValueError(f"cache resource range is invalid for {item.get('name', '<unnamed>')}")
        payload = raw[offset : offset + size]
        if item.get("sha256") != sha256(payload):
            raise ValueError(f"cache resource checksum mismatch for {item.get('name', '<unnamed>')}")
        item = dict(item, sha256=sha256(payload), container=normalize_container(item.get("container", "")))
        key = (str(item["name"]).casefold(), item["container"])
        if key in by_full:
            raise ValueError(f"duplicate cache name/container key: {item['name']}")
        by_full[key] = item
        by_name[key[0]].append(item)
    return by_full, by_name


def select_cache_record(
    name: str, container: str,
    by_full: dict[tuple[str, str], dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str]:
    exact = by_full.get((name.casefold(), normalize_container(container)))
    if exact is not None:
        return exact, "name+container"
    candidates = by_name.get(name.casefold(), [])
    if len(candidates) == 1:
        return candidates[0], "unique-name"
    return None, "ambiguous-name" if candidates else "missing-source"


def bundle_payloads(data: bytes, offset: int) -> dict[int, dict[str, Any]]:
    bundle = next(payload for current, payload in iter_unityfs_slices(data) if current == offset)
    env = UnityPy.load(bundle)
    files = getattr(env.file, "files", {})
    result: dict[int, dict[str, Any]] = {}
    for obj in env.objects:
        if obj.type.name != "AudioClip":
            continue
        clip = obj.read()
        resource = getattr(clip, "m_Resource", None)
        source = resource_basename(getattr(resource, "m_Source", "")) if resource else ""
        if not source or source not in files:
            continue
        raw = bytes(files[source].bytes)
        start = int(getattr(resource, "m_Offset", 0) or 0)
        size = int(getattr(resource, "m_Size", 0) or 0)
        if start < 0 or size <= 0 or start + size > len(raw):
            raise ValueError(f"invalid AudioClip resource range: {clip.m_Name}")
        payload = raw[start : start + size]
        result[int(obj.path_id)] = {
            "name": str(clip.m_Name), "container": normalize_container(obj.container),
            "sha256": sha256(payload), "bytes": len(payload), "signature": clip_signature(clip),
            "length": float(clip.m_Length),
        }
    return result


def npc_group(name: str) -> str:
    match = re.match(r"^Vo_(?:NPC|Boss)_([^_]+)", name, re.IGNORECASE)
    return match.group(1).casefold() if match else "<other>"


def audit(*, package: Path, index_path: Path, original: Path, installed: Path) -> dict[str, Any]:
    by_full, by_name = cache_records(package)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("format") != 1:
        raise ValueError("unsupported voice index format")
    rows: list[dict[str, Any]] = []
    for file_record in index.get("files", []):
        clips = file_record.get("clips", [])
        if not clips:
            continue
        relative = str(file_record["path"]).replace("\\", "/")
        original_path = (original / relative).resolve()
        installed_path = (installed / relative).resolve()
        if original.resolve() not in original_path.parents or installed.resolve() not in installed_path.parents:
            raise ValueError(f"index path escapes client root: {relative}")
        original_data = original_path.read_bytes()
        if file_record.get("sha256") and sha256(original_data) != file_record["sha256"]:
            raise ValueError(f"index checksum no longer matches original blob: {relative}")
        installed_data = installed_path.read_bytes()
        original_slices = list(iter_unityfs_slices(original_data))
        installed_slices = list(iter_unityfs_slices(installed_data))
        original_offsets = {offset: ordinal for ordinal, (offset, _) in enumerate(original_slices)}
        offsets = sorted({int(item["bundle_offset"]) for item in clips})
        if any(offset not in original_offsets for offset in offsets):
            raise ValueError(f"index bundle offset is absent from original blob: {relative}")
        original_payloads = {offset: bundle_payloads(original_data, offset) for offset in offsets}
        installed_payloads = {
            offset: bundle_payloads(installed_data, installed_slices[original_offsets[offset]][0])
            for offset in offsets
            if original_offsets[offset] < len(installed_slices)
        }
        for item in clips:
            name, container = str(item["name"]), normalize_container(item.get("container", ""))
            cache, match_kind = select_cache_record(name, container, by_full, by_name)
            cache_candidates = by_name.get(name.casefold(), [])
            original_row = original_payloads[int(item["bundle_offset"])].get(int(item["path_id"]))
            installed_row = installed_payloads.get(int(item["bundle_offset"]), {}).get(int(item["path_id"]))
            if not original_row or original_row.get("name") != name or original_row.get("container") != container:
                raise ValueError(f"index AudioClip name/container no longer matches original object: {relative}:{item['path_id']}")
            if installed_row and (installed_row.get("name") != name or installed_row.get("container") != container):
                installed_row = None
            rows.append({
                "name": name, "container": container, "blob": relative,
                "bundle_offset": int(item["bundle_offset"]), "path_id": int(item["path_id"]),
                "cache_match": match_kind,
                "cache_candidate_count": len(cache_candidates),
                "cache_source_blob": cache.get("source_blob") if cache else None,
                "cache_sha256": cache.get("sha256") if cache else None,
                "original_sha256": original_row.get("sha256") if original_row else None,
                "installed_sha256": installed_row.get("sha256") if installed_row else None,
                "source_payload_identical": bool(cache and original_row and cache["sha256"] == original_row["sha256"]),
                "installed_matches_cache": bool(cache and installed_row and cache["sha256"] == installed_row["sha256"]),
                "installed_matches_original": bool(original_row and installed_row and original_row["sha256"] == installed_row["sha256"]),
                "installed_object_missing": installed_row is None,
            })
    def counts(subset: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "objects": len(subset),
            "missing_cache": sum(x["cache_match"] == "missing-source" for x in subset),
            "ambiguous_cache": sum(x["cache_match"] == "ambiguous-name" for x in subset),
            "source_payload_identical": sum(x["source_payload_identical"] for x in subset),
            "installed_matches_cache": sum(x["installed_matches_cache"] for x in subset),
            "installed_matches_original": sum(x["installed_matches_original"] for x in subset),
            "installed_mismatch_cache": sum(x["cache_sha256"] is not None and not x["installed_matches_cache"] for x in subset),
            "installed_object_missing": sum(x["installed_object_missing"] for x in subset),
        }
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[npc_group(row["name"])].append(row)
    return {
        "format": 1, "package": str(package), "index": str(index_path),
        "original": str(original), "installed": str(installed),
        "summary": counts(rows),
        "groups": {name: counts(items) for name, items in sorted(grouped.items())},
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(package=args.package.resolve(), index_path=args.index.resolve(), original=args.original.resolve(), installed=args.installed.resolve())
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
