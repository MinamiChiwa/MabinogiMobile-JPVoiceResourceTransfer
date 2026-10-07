#!/usr/bin/env python3
"""Parallel cache builder for the bundled JP compressed AudioClip resources."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def process_blob(args: tuple[str, str]) -> tuple[list[dict], dict[str, int | list[str]]]:
    root_text, blob_text = args
    from transfer_jp_voice_resources import (
        clip_signature,
        is_voice_clip,
        iter_unityfs_slices,
        normalize_container,
        resource_basename,
    )
    import UnityPy

    root = Path(root_text)
    blob_path = Path(blob_text)
    relative_blob = str(blob_path.relative_to(root)).replace("\\", "/")
    records: dict[tuple[str, str], dict] = {}
    bundle_count = 0
    audio_count = 0
    skipped = 0
    engines: set[str] = set()
    for bundle_offset, bundle_data in iter_unityfs_slices(blob_path.read_bytes()):
        try:
            env = UnityPy.load(bundle_data)
        except Exception:
            skipped += 1
            continue
        bundle_count += 1
        engine = str(getattr(env.file, "version_engine", ""))
        if engine:
            engines.add(engine)
        files = getattr(env.file, "files", {})
        for obj in env.objects:
            if obj.type.name != "AudioClip":
                continue
            try:
                clip = obj.read()
                name = str(getattr(clip, "m_Name", "") or "")
                container = normalize_container(obj.container)
                if not name or not is_voice_clip(name, container):
                    continue
                audio_count += 1
                resource = getattr(clip, "m_Resource", None)
                source = getattr(resource, "m_Source", "") if resource else ""
                resource_name = resource_basename(source)
                if not resource_name or resource_name not in files:
                    skipped += 1
                    continue
                offset = int(getattr(resource, "m_Offset", 0) or 0)
                size = int(getattr(resource, "m_Size", 0) or 0)
                raw = bytes(files[resource_name].bytes)
                payload = raw[offset : offset + size]
                if size <= 0 or len(payload) != size:
                    skipped += 1
                    continue
                record = {
                    "name": name,
                    "container": container,
                    "length": float(getattr(clip, "m_Length", 0.0) or 0.0),
                    "signature": clip_signature(clip),
                    "resource_name": resource_name,
                    "source_blob": relative_blob,
                    "source_offset": bundle_offset,
                    "path_id": int(obj.path_id),
                    "payload": payload,
                }
                key = (name.casefold(), container)
                previous = records.get(key)
                if previous is None or len(payload) > len(previous["payload"]):
                    records[key] = record
            except Exception:
                skipped += 1
    return list(records.values()), {"bundles": bundle_count, "audio": audio_count, "skipped": skipped, "engines": sorted(engines)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jp-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2))))
    args = parser.parse_args()
    root = args.jp_root.resolve()
    output = args.output.resolve()
    from transfer_jp_voice_resources import iter_blob_files, sha256_bytes

    blobs = list(iter_blob_files(root))
    merged: dict[tuple[str, str], dict] = {}
    total_bundles = total_audio = total_skipped = 0
    engines: set[str] = set()
    work = [(str(root), str(path)) for path in blobs]
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(process_blob, item) for item in work]
        for index, future in enumerate(as_completed(futures), 1):
            records, stats = future.result()
            total_bundles += int(stats["bundles"])
            total_audio += int(stats["audio"])
            total_skipped += int(stats["skipped"])
            engines.update(stats["engines"])
            for record in records:
                key = (record["name"].casefold(), record["container"])
                previous = merged.get(key)
                if previous is None or len(record["payload"]) > len(previous["payload"]):
                    merged[key] = record
            if index == 1 or index % 25 == 0 or index == len(futures):
                print(f"completed blobs {index}/{len(futures)}, clips={len(merged)}", flush=True)
    output.mkdir(parents=True, exist_ok=True)
    payload = bytearray()
    records = []
    for record in sorted(merged.values(), key=lambda item: (item["name"].casefold(), item["container"])):
        raw = record.pop("payload")
        offset = len(payload)
        payload.extend(raw)
        record.update({"offset": offset, "size": len(raw), "sha256": sha256_bytes(raw)})
        records.append(record)
    (output / "resource_cache.bin").write_bytes(bytes(payload))
    metadata = {
        "format": 1,
        "source": "MabinogiMobile_JP",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "engine_versions": sorted(engines),
        "clip_count": len(records),
        "payload_bytes": len(payload),
        "scan": {"blob_count": len(blobs), "bundle_count": total_bundles, "audio_object_count": total_audio, "skipped_count": total_skipped},
        "clips": records,
    }
    (output / "resource_cache.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} compressed clips ({len(payload)} bytes) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
