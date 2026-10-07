"""Probe full-length FSB compression and preserve original bundle file layout.

Writes only diagnostic FSB/UnityFS files. Does not create or activate a client.
The encoder's nominal rate selects supported codebooks; the FSB playback rate
describes the resampled PCM. Compare against the same encoded Ogg decoded at
its nominal rate to detect mismatched codebooks independently of audio quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct
import sys
import zlib

from audit_voice_baseline import (
    bundle_at, compare_pcm, isolated_fmod, pcm_metrics, unityfs_layout,
)


def fsb_from_packets(module, template: bytes, packets: list[bytes], pcm: bytes,
                     channels: int, playback_rate: int) -> bytes:
    template = module.retarget_fsb5_template(template, channels, playback_rate)
    info = module.parse_fsb5(template)
    if info["payload_start"] != info["data_start"]:
        raise ValueError("Probe requires a single sample at offset zero")
    if len(pcm) % (2 * channels):
        raise ValueError("PCM ends within a frame")
    custom = bytearray()
    for packet in packets[3:]:
        if not 0 < len(packet) <= 65535:
            raise ValueError("Vorbis packet does not fit a uint16 FSB length")
        custom.extend(struct.pack("<H", len(packet)))
        custom.extend(packet)
    custom.extend(b"\0\0")
    custom.extend(b"\0" * (-len(custom) % 16))
    header = bytearray(template[:info["header_size"]])
    struct.pack_into("<I", header, 20, len(custom))
    # Explicit frequency metadata avoids disagreement between old third-party
    # compact-rate tables and FMOD (for example code 3: 11025 versus 12000).
    chunks = []
    cursor = info["sample_header_start"] + 8
    more = bool(info["packed"] & 1)
    while more:
        raw = struct.unpack_from("<I", template, cursor)[0]
        cursor += 4
        chunk_size, chunk_type = (raw >> 1) & 0xFFFFFF, raw >> 25
        payload = template[cursor:cursor + chunk_size]
        cursor += chunk_size
        more = bool(raw & 1)
        if chunk_type == 11:
            # Remove old seek records, retain an explicit zero seek count.
            payload = struct.pack("<II", zlib.crc32(packets[2]) & 0xFFFFFFFF, 0)
        elif chunk_type == 2:
            continue
        chunks.append((chunk_type, payload))
    chunks.append((2, struct.pack("<I", playback_rate)))
    frames = len(pcm) // (2 * channels)
    if frames >= 1 << 30:
        raise ValueError("FSB frame count is too large")
    packed = (info["packed"] & ~(((1 << 30) - 1) << 34)) | (frames << 34)
    sample = bytearray(struct.pack("<Q", packed | 1))
    for index, (chunk_type, payload) in enumerate(chunks):
        sample.extend(struct.pack("<I", (chunk_type << 25) | (len(payload) << 1) | (index + 1 < len(chunks))))
        sample.extend(payload)
    sample.extend(b"\0" * (-len(sample) % 16))
    struct.pack_into("<I", header, 12, len(sample))
    return bytes(header) + bytes(sample) + template[info["sample_header_end"]:info["data_start"]] + custom


def patch_bundle_layout(original: bytes, module, unitypy, path_id: int,
                        fsb: bytes) -> tuple[bytes, dict]:
    import lz4.block
    from UnityPy.helpers import CompressionHelper
    from UnityPy.streams import EndianBinaryReader

    layout = unityfs_layout(original)
    if layout["version"] != 8 or layout["flags"] != 579:
        raise ValueError("This diagnostic only handles the observed UnityFS v8/0x243 layout")
    if layout["uncompressed_data_hash"] != "0" * 32:
        raise ValueError("Nonzero bundle hash needs a separate validated update policy")
    header_offset = original.index(b"\0", original.index(b"\0", 12) + 1) + 1
    info_offset = layout["info_start"]
    info_end = info_offset + layout["info_size"]
    data_offset = (info_end + 15) // 16 * 16
    table = CompressionHelper.decompress_lz4(
        original[info_offset:info_end], struct.unpack_from(">I", original, header_offset + 12)[0]
    )
    original_blocks = []
    cursor = data_offset
    raw_data = bytearray()
    for block in layout["blocks"]:
        stored = original[cursor:cursor + block["compressed"]]
        cursor += block["compressed"]
        mode = block["flags"] & 63
        if mode in (2, 3):
            raw = lz4.block.decompress(stored, uncompressed_size=block["uncompressed"])
        elif mode == 0:
            raw = stored
        else:
            raise ValueError("Unexpected data block compression")
        if len(raw) != block["uncompressed"]:
            raise ValueError("Invalid original block size")
        original_blocks.append((stored, raw, block))
        raw_data.extend(raw)

    env = unitypy.load(original)
    target = next(obj for obj in env.objects if obj.path_id == path_id)
    clip = target.read()
    resource = clip.m_Resource
    entries = {item["path"]: item for item in layout["entries"]}
    serialized = entries[target.assets_file.name]
    resource_name = module.resource_basename(resource.m_Source)
    resource_entry = entries[resource_name]
    old_size = int(resource.m_Size)
    if len(fsb) > old_size:
        raise ValueError(f"FSB exceeds the original m_Size ({len(fsb)} > {old_size})")
    audio_start = resource_entry["offset"] + int(resource.m_Offset)
    audio_end = audio_start + old_size
    if not resource_entry["offset"] <= audio_start <= audio_end <= resource_entry["offset"] + resource_entry["size"]:
        raise ValueError("Audio slot exceeds its resource file")
    raw_data[audio_start:audio_end] = fsb + b"\0" * (old_size - len(fsb))
    fsb_info = module.parse_fsb5(fsb)
    resource.m_Size = len(fsb)
    clip.m_Channels = fsb_info["channels"]
    clip.m_Frequency = fsb_info["frequency"]
    clip.m_Length = fsb_info["sample_count"] / fsb_info["frequency"]
    old_object = target.get_raw_data()
    clip.save()
    # UnityPy get_raw_data/read access the original reader even after save();
    # serialized changes are in ObjectReader.data until the file is repacked.
    new_object = target.data
    if new_object is None:
        raise ValueError("AudioClip.save did not provide serialized object bytes")
    if len(new_object) != len(old_object):
        raise ValueError("AudioClip serialization changed its object length")
    object_start = serialized["offset"] + target.byte_start
    object_end = object_start + len(old_object)
    if raw_data[object_start:object_end] != old_object:
        raise ValueError("Object byte offset is not relative to its serialized file")
    raw_data[object_start:object_end] = new_object

    new_table = bytearray(table)
    new_blocks = []
    raw_cursor = 0
    changed = []
    for index, (stored, raw, block) in enumerate(original_blocks):
        replacement = bytes(raw_data[raw_cursor:raw_cursor + len(raw)])
        raw_cursor += len(raw)
        if replacement != raw:
            mode = block["flags"] & 63
            stored = (lz4.block.compress(replacement, mode="high_compression", compression=12,
                                        store_size=False) if mode in (2, 3) else replacement)
            changed.append(index)
        struct.pack_into(">I", new_table, 20 + index * 10 + 4, len(stored))
        new_blocks.append(stored)
    encoded_table = lz4.block.compress(bytes(new_table), mode="high_compression", compression=12, store_size=False)
    if len(encoded_table) > data_offset - info_offset:
        raise ValueError("Updated table exceeds its original aligned region")
    output = bytearray(original[:info_offset])
    struct.pack_into(">I", output, header_offset + 8, len(encoded_table))
    output.extend(encoded_table)
    output.extend(b"\0" * (data_offset - len(output)))
    output.extend(b"".join(new_blocks))
    needed = len(output)
    if needed > len(original):
        raise ValueError(f"Updated blocks exceed original bundle size ({needed} > {len(original)})")
    output.extend(b"\0" * (len(original) - len(output)))
    output = bytes(output)
    checked = unitypy.load(output)
    before_objects = {(obj.assets_file.name, obj.path_id): obj for obj in unitypy.load(original).objects}
    after_objects = {(obj.assets_file.name, obj.path_id): obj for obj in checked.objects}
    if before_objects.keys() != after_objects.keys():
        raise ValueError("Object inventory changed")
    for key, obj in before_objects.items():
        if key != (target.assets_file.name, target.path_id) and obj.get_raw_data() != after_objects[key].get_raw_data():
            raise ValueError(f"Non-target object changed: {key}")
    reloaded = after_objects[(target.assets_file.name, target.path_id)].read()
    checked_resource = bytes(checked.file.files[resource_name].bytes)
    old_resource = bytes(unitypy.load(original).file.files[resource_name].bytes)
    start = int(resource.m_Offset)
    if checked_resource[:start] != old_resource[:start] or checked_resource[start + old_size:] != old_resource[start + old_size:]:
        raise ValueError("Bytes outside the selected resource slot changed")
    if checked_resource[start:start + reloaded.m_Resource.m_Size] != fsb:
        raise ValueError("Selected FSB changed after repacking")
    after_layout = unityfs_layout(output)
    if after_layout["entries"] != layout["entries"]:
        raise ValueError("Bundle directory entries changed")
    return output, {
        "bundle_bytes": len(original), "repacked_bytes_before_padding": needed,
        "directory_entries_equal": True, "data_start_equal": True,
        "non_target_objects_equal": True, "outside_audio_slot_equal": True,
        "modified_blocks": changed, "before_layout": layout, "after_layout": after_layout,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--tw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.package.resolve()))
    import transfer_jp_voice_resources as module
    import UnityPy

    source = args.audit_dir / "Vo_NPC_Amy_Brrrr_01-jp-original.fsb"
    original = source.read_bytes()
    pcm, channels, rate = module.decode_fsb5_pcm(original)
    lookup = runpy.run_path(str(Path(__file__).parent / "python-fsb5/fsb5/vorbis_headers.py"))["lookup"]
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    fitting = []
    for playback_rate in (22050, 16000, 11025):
        converted = module.resample_pcm(pcm, channels, rate, channels, playback_rate)
        qualities = (0, 0.25, 0.5, 0.75, 1, 3, 6) if playback_rate == 11025 else (0, 1, 3, 6)
        for quality in qualities:
            encoder_rate = 44100
            ogg = module.run_ffmpeg([
                "-f", "s16le", "-ar", str(encoder_rate), "-ac", str(channels), "-i", "pipe:0",
                "-c:a", "libvorbis", "-q:a", str(quality), "-f", "ogg", "pipe:1",
            ], converted)
            packets = module.ogg_packets(ogg)
            crc = zlib.crc32(packets[2]) & 0xFFFFFFFF
            matching = lookup.get(crc) == packets[2]
            candidate = fsb_from_packets(module, original, packets, converted, channels, playback_rate)
            path = args.output / f"Amy-full-{playback_rate}-q{quality}.fsb"
            path.write_bytes(candidate)
            record = {
                "path": str(path), "bytes": len(candidate), "capacity": 6016,
                "encoder_rate": encoder_rate, "playback_rate": playback_rate,
                "quality": quality, "setup_crc": hex(crc), "setup_bytes_match": matching,
                "sha256": hashlib.sha256(candidate).hexdigest(),
                "source_duration": len(pcm) / (2 * channels * rate),
                "duration": len(converted) / (2 * channels * playback_rate),
            }
            decoded, decoded_ch, decoded_rate = module.decode_fsb5_pcm(candidate)
            reference = module.run_ffmpeg(["-i", "pipe:0", "-f", "s16le", "pipe:1"], ogg)
            record["vgmstream"] = pcm_metrics(decoded, decoded_ch, decoded_rate)
            record["pcm_vs_encoded_ogg"] = compare_pcm(reference, decoded)
            record["pcm_vs_encoded_ogg"]["reference_frames"] = len(reference) // (2 * channels)
            record["pcm_vs_encoded_ogg"]["fsb_frames"] = len(decoded) // (2 * channels)
            record["pcm_vs_resampled_source"] = compare_pcm(converted, decoded)
            record["fmod"] = isolated_fmod(path)
            records.append(record)
            fmod_audio = record["fmod"].get("wavs", [])
            fmod_format_ok = len(fmod_audio) == 1 and (
                fmod_audio[0]["channels"], fmod_audio[0]["frequency"], fmod_audio[0]["frames"]
            ) == (channels, playback_rate, len(converted) // (2 * channels))
            record["fmod_format_matches"] = fmod_format_ok
            print(json.dumps({k: record[k] for k in (
                "path", "bytes", "duration", "setup_bytes_match", "fmod_format_matches"
            )}), flush=True)
            if len(candidate) <= 6016 and matching and fmod_format_ok and record["pcm_vs_encoded_ogg"]["correlation"] > 0.999:
                fitting.append((record, candidate))

    if fitting:
        tw_data = args.tw_root / "appdata" if (args.tw_root / "appdata").is_dir() else args.tw_root
        # Reuse the proven baseline location; validate the object's identity.
        bundle = bundle_at(module, tw_data / "AssetPackages/0515.blob", 2455270)
        env = UnityPy.load(bundle)
        obj = next(obj for obj in env.objects if obj.path_id == 4116516575601495025)
        if obj.read().m_Name != "Vo_NPC_Amy_Brrrr_01":
            raise ValueError("TW baseline does not match the audited Amy clip")

        # Encoding size is only a first filter.  Repacking the changed UnityFS
        # blocks can still exceed the original bundle, so test candidates in
        # preference order and keep the first one that passes the full layout
        # check.  A failed best-quality candidate must not abort the search.
        selected = None
        fsb = None
        rejected = []
        for candidate_record, candidate_fsb in sorted(
            fitting,
            key=lambda pair: (pair[0]["playback_rate"], pair[0]["quality"]),
            reverse=True,
        ):
            try:
                patched, checks = patch_bundle_layout(
                    bundle, module, UnityPy, obj.path_id, candidate_fsb
                )
            except Exception as exc:
                rejected.append({
                    "path": candidate_record["path"],
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            fsb = candidate_fsb
            bundle_path = args.output / "Amy-full-preserved.bundle"
            bundle_path.write_bytes(patched)
            selected = {
                **candidate_record,
                "bundle_path": str(bundle_path),
                "bundle_checks": checks,
                "rejected_layout_candidates": rejected,
            }
            break
        # Save short listening references without modifying the original audio.
        import wave

        if selected is not None and fsb is not None:
            for name, samples, sample_rate in (
                ("Amy-jp-original.wav", pcm, rate),
                ("Amy-full-slot-candidate.wav", *module.decode_fsb5_pcm(fsb)[::2]),
            ):
                with wave.open(str(args.output / name), "wb") as wav:
                    wav.setnchannels(channels)
                    wav.setsampwidth(2)
                    wav.setframerate(sample_rate)
                    wav.writeframes(samples)
    else:
        selected = None
    report = {"format": 1, "records": records, "selected": selected,
              "client_files_modified": False, "quality_approved": False, "game_tested": False}
    (args.output / "feasibility.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": selected, "game_tested": False}), flush=True)
    return 0 if selected else 2


if __name__ == "__main__":
    raise SystemExit(main())
