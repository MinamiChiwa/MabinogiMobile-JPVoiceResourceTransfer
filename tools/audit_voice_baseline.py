"""Audit existing voice experiments without changing either client.

Native FMOD decoding runs in a separate process for each FSB so malformed
packets cannot terminate the audit or mask a working original-resource baseline.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import wave
import zlib


def pcm_metrics(pcm: bytes, channels: int, frequency: int) -> dict:
    import numpy as np

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return {
        "channels": channels,
        "frequency": frequency,
        "frames": len(samples) // channels,
        "duration": len(samples) / (channels * frequency),
        "rms": float(np.sqrt(np.mean(samples * samples))) if samples.size else 0,
        "peak": float(np.max(np.abs(samples))) if samples.size else 0,
        "zero_fraction": float(np.mean(samples == 0)) if samples.size else 1,
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
    }


def compare_pcm(expected: bytes, actual: bytes) -> dict:
    import numpy as np

    reference = np.frombuffer(expected, dtype="<i2").astype(np.float64)
    decoded = np.frombuffer(actual, dtype="<i2").astype(np.float64)
    count = min(reference.size, decoded.size)
    if not count:
        raise ValueError("Cannot compare empty PCM")
    reference, decoded = reference[:count], decoded[:count]
    noise = float(np.sum((reference - decoded) ** 2))
    signal = float(np.sum(reference ** 2))
    return {
        "sample_count_equal": len(expected) == len(actual),
        "correlation": float(np.corrcoef(reference, decoded)[0, 1]),
        "snr_db": float(10 * np.log10(signal / noise)) if noise and signal else None,
        "saturated_samples": int(np.sum((decoded == -32768) | (decoded == 32767))),
    }


def fmod_worker(path: Path) -> int:
    from fmod_toolkit import fmod

    raw = path.read_bytes()
    system, _ = fmod.get_pyfmodex_system_instance(1, fmod.pyfmodex.flags.INIT_FLAGS.NORMAL)
    result = {
        "runtime_version": hex(system.version),
        "dll_path": os.environ.get("PYFMODEX_DLL_PATH"),
    }
    try:
        decoded = fmod.raw_to_wav(raw, path.stem, 1, 44100)
        result["wavs"] = []
        for name, data in decoded.items():
            with wave.open(io.BytesIO(data), "rb") as wav:
                if wav.getsampwidth() != 2:
                    raise ValueError("Expected PCM16")
                stats = pcm_metrics(wav.readframes(wav.getnframes()), wav.getnchannels(), wav.getframerate())
            result["wavs"].append({"name": name, "wav_bytes": len(data), **stats})
        result["ok"] = bool(result["wavs"])
    except Exception as exc:
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 2


def isolated_fmod(path: Path) -> dict:
    try:
        completed = subprocess.run(
            [sys.executable, "-B", __file__, "--fmod-worker", str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "FMOD decoder exceeded 30 seconds"}
    try:
        value = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        value = {"ok": False, "stdout": completed.stdout[-1000:]}
    return {**value, "exit_code": completed.returncode, "stderr": completed.stderr[-1000:]}


def bundle_at(module, path: Path, offset: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        header = handle.read(128)
        first = header.index(b"\0", 12)
        second = header.index(b"\0", first + 1)
        size = int.from_bytes(header[second + 1:second + 9], "big")
        handle.seek(offset)
        data = handle.read(size)
    if len(data) != size or data[:7] != b"UnityFS":
        raise ValueError("Invalid selected UnityFS slice")
    return data


def resource_from_bundle(module, unitypy, data: bytes, path_id: int) -> bytes:
    env = unitypy.load(data)
    obj = next(obj for obj in env.objects if obj.path_id == path_id)
    clip = obj.read()
    res = clip.m_Resource
    raw = env.file.files[module.resource_basename(res.m_Source)].bytes
    return raw[res.m_Offset:res.m_Offset + res.m_Size]


def unityfs_layout(data: bytes) -> dict:
    from UnityPy.helpers import CompressionHelper
    from UnityPy.streams import EndianBinaryReader

    reader = EndianBinaryReader(data)
    signature = reader.read_string_to_null()
    version = reader.read_u_int()
    player = reader.read_string_to_null()
    engine = reader.read_string_to_null()
    size = reader.read_long()
    compressed = reader.read_u_int()
    uncompressed = reader.read_u_int()
    flags = reader.read_u_int()
    if version >= 7:
        reader.align_stream(16)
    start = reader.Position
    if flags & 0x80:
        reader.Position = len(data) - compressed
    info = reader.read_bytes(compressed)
    compression = flags & 0x3F
    if compression in (2, 3):
        info = CompressionHelper.decompress_lz4(info, uncompressed)
    elif compression == 1:
        info = CompressionHelper.decompress_lzma(info)
    elif compression != 0:
        raise ValueError(f"Unsupported block table compression {compression}")
    table = EndianBinaryReader(info)
    digest = table.read_bytes(16)
    blocks = [
        {"uncompressed": table.read_u_int(), "compressed": table.read_u_int(), "flags": table.read_u_short()}
        for _ in range(table.read_int())
    ]
    entries = [
        {"offset": table.read_long(), "size": table.read_long(), "flags": table.read_u_int(), "path": table.read_string_to_null()}
        for _ in range(table.read_int())
    ]
    return {
        "signature": signature, "version": version, "player": player, "engine": engine,
        "size": size, "flags": flags, "info_start": start, "info_size": compressed,
        "uncompressed_data_hash": digest.hex(), "blocks": blocks, "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fmod-worker", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--tw-root", type=Path)
    parser.add_argument("--test-copy", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.fmod_worker:
        return fmod_worker(args.fmod_worker)
    if not all((args.package, args.tw_root, args.output)):
        parser.error("--package, --tw-root and --output are required")
    sys.path.insert(0, str(args.package.resolve()))
    import transfer_jp_voice_resources as module
    import UnityPy

    args.output.mkdir(parents=True, exist_ok=True)
    tw_data = args.tw_root / "appdata" if (args.tw_root / "appdata").is_dir() else args.tw_root
    metadata = json.loads((args.package / "resource_cache.json").read_text(encoding="utf-8"))
    cache = (args.package / "resource_cache.bin").read_bytes()
    records = []
    structures = []
    comparisons = []
    lookup_path = Path(__file__).parent / "python-fsb5" / "fsb5" / "vorbis_headers.py"
    lookup = runpy.run_path(str(lookup_path))["lookup"] if lookup_path.is_file() else {}

    def inspect(label: str, name: str, raw: bytes, extra: dict | None = None):
        path = args.output / f"{name}-{label}.fsb"
        path.write_bytes(raw)
        info = module.parse_fsb5(raw)
        record = {
            "name": name, "label": label, "bytes": len(raw), "sha256": module.sha256_bytes(raw),
            "fsb": {k: info[k] for k in ("channels", "frequency", "sample_count")},
            "setup_crc": hex(info["crc"]), **(extra or {}),
        }
        try:
            pcm, ch, rate = module.decode_fsb5_pcm(raw)
            record["vgmstream"] = {"ok": True, **pcm_metrics(pcm, ch, rate)}
        except Exception as exc:
            record["vgmstream"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        record["fmod"] = isolated_fmod(path)
        records.append(record)
        print(json.dumps({"name": name, "label": label, "setup_crc": record["setup_crc"],
                          "vgmstream": record["vgmstream"], "fmod": record["fmod"]}), flush=True)

    for copy in args.test_copy:
        test_data = copy / "appdata" if (copy / "appdata").is_dir() else copy
        report = json.loads((test_data / "patch-report.json").read_text(encoding="utf-8"))
        for item in report["replacements"]:
            if item["status"] != "replaced":
                continue
            name = item["name"]
            original = bundle_at(module, tw_data / item["blob"], item["bundle_offset"])
            modified = bundle_at(module, test_data / item["blob"], item["bundle_offset"])
            structures.append({"name": name, "copy": str(copy), "original": unityfs_layout(original),
                               "modified": unityfs_layout(modified)})
            inspect("tw-original", name, resource_from_bundle(module, UnityPy, original, item["path_id"]))
            cached = next(x for x in metadata["clips"] if x["name"] == name and x["container"] == item["container"])
            jp = cache[cached["offset"]:cached["offset"] + cached["size"]]
            inspect("jp-original", name, jp)
            inspect("existing-patch", name, resource_from_bundle(module, UnityPy, modified, item["path_id"]))
            pcm, ch, rate = module.decode_fsb5_pcm(jp)
            for quality in (1.0, 6.0):
                ogg = module.run_ffmpeg([
                    "-f", "s16le", "-ar", str(rate), "-ac", str(ch), "-i", "pipe:0",
                    "-c:a", "libvorbis", "-q:a", str(quality), "-f", "ogg", "pipe:1",
                ], pcm)
                setup = module.ogg_packets(ogg)[2]
                actual_crc = zlib.crc32(setup) & 0xFFFFFFFF
                expected = module.run_ffmpeg(["-i", "pipe:0", "-f", "s16le", "pipe:1"], ogg)
                generated = module.encode_fsb5_at_quality(pcm, ch, rate, quality, jp)
                inspect(f"ffmpeg-q{quality:g}-old-crc", name, generated,
                        {"generated_setup_crc": hex(actual_crc), "quality": quality,
                         "generated_setup_matches_lookup": lookup.get(actual_crc) == setup})
                fixed = bytearray(generated)
                struct.pack_into("<I", fixed, module.parse_fsb5(generated)["crc_offset"], actual_crc)
                inspect(f"ffmpeg-q{quality:g}-actual-crc", name, bytes(fixed),
                        {"generated_setup_crc": hex(actual_crc), "quality": quality,
                         "generated_setup_matches_lookup": lookup.get(actual_crc) == setup})
                for label, raw in (("old-crc", generated), ("actual-crc", bytes(fixed))):
                    try:
                        decoded, _, _ = module.decode_fsb5_pcm(raw)
                        comparisons.append({"name": name, "quality": quality, "label": label,
                                            **compare_pcm(expected, decoded)})
                    except Exception as exc:
                        comparisons.append({"name": name, "quality": quality, "label": label,
                                            "error": f"{type(exc).__name__}: {exc}"})

    lib_path = Path(__file__).parent / "fsb5dll" / "libvorbis.dll"
    libraries = {}
    if lib_path.is_file():
        library = ctypes.CDLL(str(lib_path.resolve()))
        library.vorbis_version_string.restype = ctypes.c_char_p
        libraries["vorbis"] = {"path": str(lib_path), "sha256": module.sha256_file(lib_path),
                               "version": library.vorbis_version_string().decode("ascii")}
    result = {"format": 1, "python": sys.version, "package": str(args.package),
              "tw_root": str(args.tw_root), "records": records, "structures": structures,
              "comparisons": comparisons, "libraries": libraries}
    (args.output / "audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
