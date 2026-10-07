"""Audit global resource-stream capacity for a selected UnityFS bundle.

This is read-only. It decodes the JP samples, generates self-consistent FSB5
candidates with the existing encoder, and computes whether a complete
resource-stream repack can fit after 16-byte alignment. It does not write a
client copy or patch a bundle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--tw-root", type=Path, required=True)
    parser.add_argument("--jp-root", type=Path, required=True)
    parser.add_argument("--blob", default="AssetPackages/0515.blob")
    parser.add_argument("--bundle-offset", type=int, default=2455270)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.package.resolve()))
    import UnityPy
    import transfer_jp_voice_resources as mod

    tw_root = args.tw_root / "appdata" if (args.tw_root / "appdata").is_dir() else args.tw_root
    jp_root = args.jp_root / "appdata" if (args.jp_root / "appdata").is_dir() else args.jp_root
    blob_path = tw_root / args.blob
    blob = blob_path.read_bytes()
    slices = list(mod.iter_unityfs_slices(blob))
    try:
        bundle = next(data for offset, data in slices if offset == args.bundle_offset)
    except StopIteration as exc:
        raise SystemExit(
            f"UnityFS bundle offset {args.bundle_offset} was not found in {blob_path}"
        ) from exc
    tw_env = UnityPy.load(bundle)

    # The cache gives stable JP bytes and names without scanning the full JP
    # client.  Fall back to the JP scan when the cache does not contain a name.
    cache_full, cache_byname, _ = mod.load_resource_cache(args.package)
    targets = []
    seen = set()
    for obj in tw_env.objects:
        if obj.type.name != "AudioClip":
            continue
        clip = obj.read()
        name = str(getattr(clip, "m_Name", ""))
        if not name.lower().startswith("vo_") and "_vo_" not in name.lower():
            continue
        resource = getattr(clip, "m_Resource", None)
        if not resource or not getattr(resource, "m_Source", ""):
            continue
        key = (name.casefold(), int(obj.path_id))
        if key in seen:
            continue
        seen.add(key)
        jp, match_kind = mod.select_jp_clip(name, str(getattr(clip, "m_Container", "")), cache_full, cache_byname)
        if jp is None:
            continue
        res_name = mod.resource_basename(resource.m_Source)
        res_file = tw_env.file.files[res_name]
        capacity = int(resource.m_Size)
        old_offset = int(resource.m_Offset)
        old_bytes = bytes(res_file.bytes)[old_offset:old_offset + capacity]
        info = mod.parse_fsb5(jp.resource_bytes)
        pcm, channels, rate = mod.decode_fsb5_pcm(jp.resource_bytes)
        targets.append({
            "name": name,
            "path_id": int(obj.path_id),
            "resource": res_name,
            "offset": old_offset,
            "capacity": capacity,
            "source_bytes": len(jp.resource_bytes),
            "channels": channels,
            "frequency": rate,
            "duration": len(pcm) / (2 * channels * rate),
            "jp": jp.resource_bytes,
            "tw_sha256": mod.sha256_bytes(old_bytes),
            "match_kind": match_kind,
        })

    if not targets:
        raise SystemExit("No matching voice clips found in selected bundle")

    # Generate a compact candidate frontier.  Every candidate is complete,
    # setup-consistent, and decoded once before it enters the report.
    rates = (44100, 22050, 16000, 11025, 8000)
    qualities = (6.0, 4.0, 2.0, 1.0, 0.5, 0.0)
    rows = []
    for target in targets:
        candidates = []
        template = target["jp"]
        source_pcm, source_channels, source_frequency = mod.decode_fsb5_pcm(template)
        for rate in rates:
            converted = mod.resample_pcm(
                source_pcm,
                source_channels,
                source_frequency,
                target["channels"],
                rate,
            )
            for quality in qualities:
                fsb = mod.encode_fsb5_at_quality(converted, target["channels"], rate, quality, mod.retarget_fsb5_template(template, target["channels"], rate), encoder_frequency=44100)
                decoded, dec_channels, dec_rate = mod.decode_fsb5_pcm(fsb)
                if (dec_channels, dec_rate, len(decoded)) != (target["channels"], rate, len(converted)):
                    continue
                candidates.append({
                    "rate": rate,
                    "quality": quality,
                    "bytes": len(fsb),
                    "frames": len(converted) // (2 * target["channels"]),
                    "duration": len(converted) / (2 * target["channels"] * rate),
                    "setup_crc": hex(mod.parse_fsb5(fsb)["crc"]),
                })
        # De-duplicate sizes while retaining the highest quality choice.
        unique = {}
        for candidate in candidates:
            key = (candidate["rate"], candidate["bytes"])
            if key not in unique or candidate["quality"] > unique[key]["quality"]:
                unique[key] = candidate
        target["candidates"] = sorted(unique.values(), key=lambda x: (x["bytes"], -x["rate"], -x["quality"]))
        rows.append({k: v for k, v in target.items() if k != "jp"})

    # For each resource stream, old layout is the baseline.  Repacking uses
    # the existing target order and 16-byte aligned starts; untouched bytes are
    # accounted for outside target intervals.
    by_resource = {}
    for target in targets:
        by_resource.setdefault(target["resource"], []).append(target)
    streams = []
    for resource, items in by_resource.items():
        items.sort(key=lambda x: x["offset"])
        original_end = max(item["offset"] + item["capacity"] for item in items)
        stream_start = min(item["offset"] for item in items)
        old_total = sum(item["capacity"] for item in items)
        # A useful lower bound: all targets packed at their smallest valid
        # candidate, with no assumption that unrelated bytes can move.
        min_total = sum(min((c["bytes"] for c in item["candidates"]), default=10**18) for item in items)
        streams.append({
            "resource": resource,
            "count": len(items),
            "first_offset": stream_start,
            "original_target_bytes": old_total,
            "minimum_candidate_bytes": min_total,
            "delta_minimum": min_total - old_total,
            "original_target_end": original_end,
            "candidates_complete": all(item["candidates"] for item in items),
        })

    report = {
        "format": 1,
        "client_files_modified": False,
        "game_tested": False,
        "bundle": args.blob,
        "bundle_offset": args.bundle_offset,
        "targets": rows,
        "streams": streams,
        "source": "JP cache and selected TW bundle",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"targets": len(rows), "streams": len(streams), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
