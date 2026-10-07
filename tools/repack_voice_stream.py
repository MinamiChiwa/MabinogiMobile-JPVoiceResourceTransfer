#!/usr/bin/env python3
"""Repack complete FSB5 voice resources inside one UnityFS resource stream.

This is a diagnostic prototype.  It writes a standalone bundle (and optionally
an otherwise-identical blob) and never edits a client directory.  Every target
AudioClip keeps its complete decoded duration; only its resource offset, size,
and matching audio metadata are changed. ``--encoder raw`` appends the exact
JP FSB5 bytes to the resource stream, bypassing the original fixed slot. It
also rewrites the custom cumulative-end table in the outer ``.blob`` header.
The manifest and package index are deliberately not changed.
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
    parser.add_argument("--blob", type=Path, required=True)
    parser.add_argument("--bundle-offset", type=int, required=True)
    parser.add_argument("--output-bundle", type=Path, required=True)
    parser.add_argument("--output-blob", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--name-prefix", default="Vo_NPC_Amy_")
    parser.add_argument("--quality", type=float, default=1.0)
    parser.add_argument("--rate", type=int, default=22050)
    parser.add_argument(
        "--encoder", choices=("fsbank", "ffmpeg", "raw"), default="fsbank",
        help="FSBank writes a native FSB5 sample; ffmpeg is retained for comparison only",
    )
    parser.add_argument(
        "--fsbank-dir", type=Path,
        default=Path(r"D:\Mabimoba\external\FSB5.Converter-research\FSBank\lib\x64"),
    )
    parser.add_argument(
        "--fsbank-cache", type=Path,
        default=Path(r"D:\Mabimoba\diagnostic\fsbank-cache"),
    )
    parser.add_argument(
        "--quality-min", type=int, default=1,
        help="lowest FSBank quality tried when searching for a fitting candidate",
    )
    parser.add_argument(
        "--quality-step", type=int, default=10,
        help="FSBank quality search step; lower values search more finely",
    )
    args = parser.parse_args()

    def file_bytes(file_obj):
        if hasattr(file_obj, "reader") and hasattr(file_obj.reader, "bytes"):
            return bytes(file_obj.reader.bytes)
        return bytes(file_obj.bytes)

    sys.path.insert(0, str(args.package.resolve()))
    import UnityPy  # type: ignore
    import transfer_jp_voice_resources as mod  # type: ignore
    if args.encoder in ("fsbank", "ffmpeg"):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from fsbank_bridge import encode_pcm  # type: ignore

    tw_root = args.tw_root.resolve()
    if (tw_root / "appdata").is_dir():
        tw_root = tw_root / "appdata"
    blob_path = (tw_root / args.blob).resolve()
    source_blob = blob_path.read_bytes()
    slices = list(mod.iter_unityfs_slices(source_blob))
    try:
        bundle_data = next(data for offset, data in slices if offset == args.bundle_offset)
    except StopIteration as exc:
        raise SystemExit(f"bundle offset not found: {blob_path}@{args.bundle_offset}") from exc

    env = UnityPy.load(bundle_data)
    cache_full, cache_by_name, cache_stats = mod.load_resource_cache(args.package.resolve())
    all_objects = {(obj.assets_file.name, int(obj.path_id)): obj for obj in env.objects}
    before_hashes = {
        key: (obj.type.name, mod.sha256_bytes(obj.get_raw_data()))
        for key, obj in all_objects.items()
    }
    original_files = {}
    for name, file_obj in env.file.files.items():
        if hasattr(file_obj, "reader") and hasattr(file_obj.reader, "bytes"):
            original_files[name] = bytes(file_obj.reader.bytes)
        elif hasattr(file_obj, "bytes"):
            original_files[name] = bytes(file_obj.bytes)

    targets = []
    for obj in env.objects:
        if obj.type.name != "AudioClip":
            continue
        clip = obj.read()
        name = str(getattr(clip, "m_Name", "") or "")
        if not name.startswith(args.name_prefix):
            continue
        resource = getattr(clip, "m_Resource", None)
        if resource is None or not getattr(resource, "m_Source", ""):
            raise ValueError(f"target has no external resource: {name}")
        jp, match_kind = mod.select_jp_clip(
            name,
            str(getattr(clip, "m_Container", "")),
            cache_full,
            cache_by_name,
        )
        if jp is None:
            raise ValueError(f"no unambiguous JP cache match: {name}")
        source_name = mod.resource_basename(resource.m_Source)
        if source_name not in env.file.files:
            raise ValueError(f"resource file missing: {source_name} ({name})")
        old_offset = int(resource.m_Offset)
        old_size = int(resource.m_Size)
        resource_file = bytes(env.file.files[source_name].bytes)
        if old_offset < 0 or old_size <= 0 or old_offset + old_size > len(resource_file):
            raise ValueError(f"resource range is invalid: {name}")
        targets.append({
            "object": obj,
            "clip": clip,
            "jp": jp,
            "match_kind": match_kind,
            "name": name,
            "resource_name": source_name,
            "old_offset": old_offset,
            "old_size": old_size,
            "path_id": int(obj.path_id),
        })

    if not targets:
        raise ValueError(f"no AudioClip names begin with {args.name_prefix!r}")
    resource_names = {item["resource_name"] for item in targets}
    if len(resource_names) != 1:
        raise ValueError(f"targets span multiple resource files: {sorted(resource_names)}")
    resource_name = next(iter(resource_names))
    resource_file = bytes(env.file.files[resource_name].bytes)
    targets.sort(key=lambda item: item["old_offset"])
    for previous, current in zip(targets, targets[1:]):
        if previous["old_offset"] + previous["old_size"] > current["old_offset"]:
            raise ValueError("target resource ranges overlap")
    original_first_offset = min(item["old_offset"] for item in targets)
    original_last_offset = max(item["old_offset"] + item["old_size"] for item in targets)
    # In raw mode, append original JP banks to the resource stream. This keeps
    # every unrelated resource byte in place and removes the fixed-slot limit.
    # The AudioClip references are repointed to the appended banks.
    if args.encoder == "raw":
        first_offset = mod.aligned(len(resource_file))
        last_offset = None
    else:
        first_offset = original_first_offset
        last_offset = original_last_offset

    replacement = bytearray(resource_file)
    allowed_ranges = []
    for item in targets:
        start = item["old_offset"]
        end = start + item["old_size"]
        if args.encoder != "raw":
            replacement[start:end] = b"\0" * item["old_size"]
        allowed_ranges.append((start, end))
    # Repacking moves variable-size payloads within the enclosing stream, so
    # every byte in the packed range is intentionally allowed to change.
    if args.encoder == "raw":
        allowed_ranges = []

    candidate_rows = []
    encoder_cache = args.fsbank_cache.resolve()
    encoder_cache.mkdir(parents=True, exist_ok=True)

    def packed_size(lengths: list[int]) -> int:
        cursor = first_offset
        for length in lengths:
            cursor = mod.aligned(cursor)
            cursor += length
        return cursor - first_offset

    write_offset = first_offset
    pending = []
    prepared = []
    for item in targets:
        jp = item["jp"]
        if args.encoder == "raw":
            prepared.append({
                "item": item,
                "jp": jp,
                "converted": None,
                "channels": int(jp.signature["channels"]),
                "frequency": int(jp.signature["frequency"]),
                "template": None,
                "candidates": {None: jp.resource_bytes},
                "selected_quality": None,
            })
            continue
        source_pcm, source_channels, source_frequency = mod.decode_fsb5_pcm(jp.resource_bytes)
        output_channels = source_channels
        converted = mod.resample_pcm(
            source_pcm,
            source_channels,
            source_frequency,
            output_channels,
            args.rate,
        )
        template = mod.retarget_fsb5_template(jp.resource_bytes, output_channels, args.rate)
        if args.encoder == "fsbank":
            quality_min = max(args.quality_min, 1)
            candidates = {}
            minimum = encode_pcm(
                converted, output_channels, args.rate, quality_min,
                args.fsbank_dir, encoder_cache,
            )
            candidates[quality_min] = minimum
            prepared.append({
                "item": item,
                "jp": jp,
                "converted": converted,
                "channels": output_channels,
                "frequency": args.rate,
                "template": template,
                "candidates": candidates,
                "selected_quality": quality_min,
            })
        else:
            payload = mod.encode_fsb5_at_quality(
                converted,
                output_channels,
                args.rate,
                args.quality,
                template,
                encoder_frequency=44100,
            )
            prepared.append({
                "item": item,
                "jp": jp,
                "converted": converted,
                "channels": output_channels,
                "frequency": args.rate,
                "template": template,
                "candidates": {args.quality: payload},
                "selected_quality": args.quality,
            })

    if args.encoder == "fsbank":
        minimum_total = packed_size([
            len(row["candidates"][row["selected_quality"]]) for row in prepared
        ])
        capacity = last_offset - first_offset
        if minimum_total > capacity:
            raise ValueError(
                f"FSBank lowest-quality complete candidates exceed target range "
                f"({minimum_total} > {capacity}); no audio was written"
            )
        step = max(args.quality_step, 1)
        quality_min = max(args.quality_min, 1)
        upgrade_qualities = list(range(100, quality_min, -step))
        if quality_min not in upgrade_qualities:
            upgrade_qualities.append(quality_min)
        for row in prepared:
            for quality in upgrade_qualities:
                if quality <= row["selected_quality"]:
                    continue
                candidate = encode_pcm(
                    row["converted"], row["channels"], row["frequency"], quality,
                    args.fsbank_dir, encoder_cache,
                )
                row["candidates"][quality] = candidate
                trial_lengths = [
                    len(other["candidates"][other["selected_quality"]])
                    for other in prepared
                ]
                current_index = prepared.index(row)
                trial_lengths[current_index] = len(candidate)
                if packed_size(trial_lengths) <= capacity:
                    row["selected_quality"] = quality

    for row in prepared:
        item = row["item"]
        jp = row["jp"]
        output_channels = row["channels"]
        quality_used = row["selected_quality"]
        payload = row["candidates"][quality_used]
        validation = mod.validate_fsb5_payload(
            payload,
            expected_duration=jp.length,
            expected_channels=output_channels,
            expected_frequency=row["frequency"],
            max_bytes=None,
        )
        write_offset = mod.aligned(write_offset)
        end = write_offset + len(payload)
        if last_offset is not None and end > last_offset:
            raise ValueError(
                f"packed stream exceeds original target range: {end - first_offset} > {last_offset - first_offset}"
            )
        if write_offset > len(replacement):
            replacement.extend(b"\0" * (write_offset - len(replacement)))
        replacement[write_offset:end] = payload
        clip = item["clip"]
        resource = clip.m_Resource
        resource.m_Offset = write_offset
        resource.m_Size = len(payload)
        clip.m_Length = jp.length
        clip.m_Channels = output_channels
        clip.m_Frequency = row["frequency"]
        clip.m_CompressionFormat = jp.signature.get("compression_format", clip.m_CompressionFormat)
        clip.save()
        pending.append((item, payload, write_offset, validation))
        candidate_rows.append({
            "name": item["name"],
            "path_id": item["path_id"],
            "old_offset": item["old_offset"],
            "old_size": item["old_size"],
            "new_offset": write_offset,
            "new_size": len(payload),
            "channels": output_channels,
            "frequency": row["frequency"],
            "quality": quality_used,
            "encoder": args.encoder,
            "source_duration": jp.length,
            "decoded_duration": validation["duration"],
            "setup_crc": hex(mod.parse_fsb5(payload)["crc"]),
            "payload_sha256": mod.sha256_bytes(payload),
            "match_kind": item["match_kind"],
        })
        write_offset = end

    if last_offset is not None and write_offset > last_offset:
        raise ValueError("packed stream write cursor exceeded target range")
    appended_range = (first_offset, write_offset) if args.encoder == "raw" else None

    from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter  # type: ignore

    changed_serialized_names = {item["object"].assets_file.name for item in targets}
    serialized_patches = {
        name: bytearray(original_files[name])
        for name in changed_serialized_names
        if name in original_files
    }
    for item in targets:
        obj = item["object"]
        name = obj.assets_file.name
        raw = getattr(obj, "data", None)
        if raw is None or name not in serialized_patches or len(raw) != int(obj.byte_size):
            raise ValueError(
                f"cannot preserve serialized object bytes: {item['name']} "
                f"raw={None if raw is None else len(raw)} byte_size={getattr(obj, 'byte_size', None)} "
                f"assets={name}"
            )
        start = int(obj.byte_start)
        serialized_patches[name][start : start + len(raw)] = raw
    for name, patched in serialized_patches.items():
        reader = EndianBinaryReader(bytes(patched))
        reader.flags = int(getattr(env.file.files[name], "flags", 0))
        reader.name = name
        env.file.files[name] = reader
    resource_obj = env.file.files[resource_name]
    writer = EndianBinaryWriter(bytes(replacement), endian=str(getattr(resource_obj, "endian", ">")))
    writer.flags = int(getattr(resource_obj, "flags", 0))
    writer.name = resource_name
    env.file.files[resource_name] = writer
    env.file.is_changed = True

    rebuilt_sizes = {}
    rebuilt_candidates = {}
    for packer in ("original", "lz4hc", "lzma"):
        rebuilt = mod.save_bundle_preserving_unchanged_files(
            env.file,
            original_files,
            changed_serialized_names,
            packer,
        )
        rebuilt_sizes[packer] = len(rebuilt)
        preserved = rebuilt if args.encoder == "raw" else mod.preserve_bundle_size(rebuilt, len(bundle_data))
        if preserved is not None:
            rebuilt_candidates[packer] = preserved
    if not rebuilt_candidates:
        raise ValueError(f"all bundle packers grew: {rebuilt_sizes}")

    # Prefer the original packer, then the highest-compression fallbacks.
    packer_used = next(p for p in ("original", "lz4hc", "lzma") if p in rebuilt_candidates)
    output_bundle = rebuilt_candidates[packer_used]
    check = UnityPy.load(output_bundle)
    after_objects = {(obj.assets_file.name, int(obj.path_id)): obj for obj in check.objects}
    if set(after_objects) != set(all_objects):
        raise ValueError("bundle object set changed")
    pending_keys = {(item["object"].assets_file.name, item["path_id"]) for item in targets}
    for key, (type_name, old_hash) in before_hashes.items():
        obj = after_objects[key]
        if obj.type.name != type_name:
            raise ValueError(f"object type changed: {key}")
        if key not in pending_keys and mod.sha256_bytes(obj.get_raw_data()) != old_hash:
            raise ValueError(f"non-target object changed: {key}")
    if set(check.file.files) != set(original_files):
        raise ValueError("UnityFS file directory changed")
    for name, before in original_files.items():
        after = file_bytes(check.file.files[name])
        if name == resource_name:
            if args.encoder == "raw":
                if len(after) < len(before) or after[: len(before)] != before:
                    raise ValueError(f"unexpected pre-existing resource bytes changed: {name}")
            else:
                mod.verify_changes_within_ranges(before, after, allowed_ranges, name)
        elif name in serialized_patches:
            if after != bytes(serialized_patches[name]):
                raise ValueError(f"unexpected serialized bytes changed: {name}")
        elif after != before:
            raise ValueError(f"non-target UnityFS file changed: {name}")
    for item, payload, expected_offset, validation in pending:
        obj = after_objects[(item["object"].assets_file.name, item["path_id"])]
        clip = obj.read()
        resource = clip.m_Resource
        actual_file = bytes(check.file.files[resource_name].bytes)
        actual = actual_file[int(resource.m_Offset) : int(resource.m_Offset) + int(resource.m_Size)]
        if actual != payload:
            raise ValueError(f"target payload mismatch after rebuild: {item['name']}")
        if int(resource.m_Offset) != expected_offset or int(resource.m_Size) != len(payload):
            raise ValueError(f"target resource reference mismatch after rebuild: {item['name']}")
        checked = mod.validate_fsb5_payload(
            actual,
            expected_duration=item["jp"].length,
            expected_channels=clip.m_Channels,
            expected_frequency=clip.m_Frequency,
            max_bytes=None,
        )
        if abs(float(clip.m_Length) - item["jp"].length) > 1e-5:
            raise ValueError(f"AudioClip duration metadata mismatch: {item['name']}")
        if checked["frames"] != validation["frames"]:
            raise ValueError(f"AudioClip frame count changed: {item['name']}")

    args.output_bundle.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output_bundle.resolve().write_bytes(output_bundle)
    if args.output_blob:
        output_blob = bytearray()
        cursor = 0
        for offset, original_bundle in slices:
            output_blob.extend(source_blob[cursor:offset])
            output_blob.extend(output_bundle if offset == args.bundle_offset else original_bundle)
            cursor = offset + len(original_bundle)
        output_blob.extend(source_blob[cursor:])
        if args.encoder == "raw":
            # The custom blob header is a table of cumulative absolute bundle
            # ends. UnityFS itself has already been rebuilt; this outer table
            # must be updated before the game can locate later bundles.
            header_size = int.from_bytes(output_blob[4:8], "little")
            if output_blob[:4] != bytes.fromhex("BA C3 A9 E5"):
                raise ValueError("unexpected .blob header magic")
            count = (header_size - 8) // 4
            rebuilt_slices = list(mod.iter_unityfs_slices(bytes(output_blob)))
            if len(rebuilt_slices) != count:
                raise ValueError(f".blob bundle count changed ({len(rebuilt_slices)} != {count})")
            ends = [offset + len(bundle) for offset, bundle in rebuilt_slices]
            starts = [header_size, *ends[:-1]]
            if [offset for offset, _ in rebuilt_slices] != starts:
                raise ValueError("rebuilt .blob has a gap or non-UnityFS bytes between bundles")
            if ends[-1] != len(output_blob):
                raise ValueError("rebuilt .blob final bundle does not reach EOF")
            for index, end in enumerate(ends):
                output_blob[8 + index * 4 : 12 + index * 4] = int(end).to_bytes(4, "little")
        args.output_blob.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output_blob.resolve().write_bytes(output_blob)

    report = {
        "format": 1,
        "client_files_modified": False,
        "game_tested": False,
        "bundle": str(args.blob).replace("\\", "/"),
        "bundle_offset": args.bundle_offset,
        "bundle_size": len(bundle_data),
        "resource": resource_name,
        "resource_size": len(resource_file),
        "target_range": [original_first_offset, original_last_offset],
        "appended_range": list(appended_range) if appended_range else None,
        "target_original_bytes": sum(item["old_size"] for item in targets),
        "target_repacked_bytes": write_offset - first_offset,
            "quality": quality_used,
            "encoder": args.encoder,
        "rate": None if args.encoder == "raw" else args.rate,
        "packer": packer_used,
        "rebuilt_sizes": rebuilt_sizes,
        "cache": cache_stats,
        "targets": candidate_rows,
        "output_bundle": str(args.output_bundle.resolve()),
        "output_blob": str(args.output_blob.resolve()) if args.output_blob else None,
    }
    args.report.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"targets": len(targets), "repacked_bytes": write_offset - first_offset, "packer": packer_used}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
