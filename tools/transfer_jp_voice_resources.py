#!/usr/bin/env python3
"""Copy Japanese AudioClip resource bytes into a separate TW client copy.

The script never edits the source clients.  Without ``--output-copy`` it only
scans and writes a report.  With ``--output-copy`` it copies the TW directory,
patches matching external AudioClip resources in that copy, and creates a
rollback script next to the generated reports.

This is an offline file transformation tool.  It does not alter manifests,
disable integrity checks, inject into the game, or make any online/anti-cheat
guarantee.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import struct
import zlib
import io
import wave
from array import array
from functools import lru_cache
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from resource_progress import emit_progress
from portable_runtime import package_root


UNITY_SIGNATURE = b"UnityFS"
VOICE_TOKEN = re.compile(r"(?:^|[_/])vo(?:[_/]|$)", re.IGNORECASE)
PACKAGE_DIRS = ("AssetPackages", "ResourcesPackage", "PlatformResourcePackages")
SUPPORTED_ENGINE_PREFIX = "2021.3."
ALIGNMENT = 16
# Below this input rate the resampler has already removed too much speech
# bandwidth for a useful voice replacement.  A target that only fits below
# this floor is reported as skipped_quality and keeps the original TW clip.
MIN_ACCEPTABLE_INPUT_FREQUENCY = 8000

# The ffmpeg and vgmstream tools are used for inspection. Native FSBank is the
# only encoder used when an oversized resource must fit its original slot.
_FFMPEG_PATH: Path | None = None
_VGMSTREAM_PATH: Path | None = None
_VORBIS_HEADER_CACHE: dict[tuple[int, int], dict[int, tuple[list[bytes], float]]] = {}
# The cache key includes the encoder backend and strategy version.  A payload
# produced by the old hand-built Vorbis path must never be reused by FSBank.
_REENCODE_CACHE: dict[tuple[Any, ...], EncodedClip] = {}
_FSBANK_BRIDGE: Any = None
_FSBANK_DIR: Path | None = None
_FSBANK_CACHE_DIR: Path | None = None
REENCODE_STRATEGY_VERSION = "vorbis-ima-zero-edge-v3"
_VALIDATED_PAYLOADS: dict[tuple[Any, ...], dict[str, Any]] = {}


@dataclass(frozen=True)
class EncodedClip:
    payload: bytes
    quality: float | None
    channels: int
    frequency: int
    duration: float
    encoder: str
    details: dict[str, Any]

    def __iter__(self):
        # Retain the original six-value unpacking interface for callers.
        return iter((self.payload, self.quality, self.channels, self.frequency, self.duration, self.encoder))


def iter_blob_files(root: Path) -> Iterator[Path]:
    for dirname in PACKAGE_DIRS:
        directory = root / dirname
        if directory.is_dir():
            yield from sorted(directory.rglob("*.blob"))


def iter_unityfs_slices(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield complete UnityFS slices from a custom concatenated .blob."""

    cursor = 0
    while True:
        offset = data.find(UNITY_SIGNATURE, cursor)
        if offset < 0:
            return
        # signature + uint32 format + two NUL-terminated version strings
        first_null = data.find(b"\0", offset + 12)
        if first_null < 0:
            return
        second_null = data.find(b"\0", first_null + 1)
        if second_null < 0 or second_null + 9 > len(data):
            return
        size = int.from_bytes(data[second_null + 1 : second_null + 9], "big")
        end = offset + size
        if size <= 0 or end > len(data):
            cursor = offset + 1
            continue
        yield offset, data[offset:end]
        cursor = end


def rewrite_blob_end_table(data: bytes) -> bytes:
    """Update the custom cumulative bundle-end table after full rebuilding.

    The package index stores a blob key and bundle ordinal; the outer blob
    header stores the corresponding absolute bundle ends. Keeping the number
    and order of bundles unchanged lets the index remain valid while allowing
    individual UnityFS bundles to grow.
    """

    if len(data) < 12 or data[:4] != bytes.fromhex("BA C3 A9 E5"):
        raise ValueError("unsupported .blob header magic")
    header_size = int.from_bytes(data[4:8], "little")
    if header_size < 12 or header_size > len(data) or (header_size - 8) % 4:
        raise ValueError(f"invalid .blob header size: {header_size}")
    count = (header_size - 8) // 4
    slices = list(iter_unityfs_slices(data))
    if len(slices) != count:
        raise ValueError(f".blob bundle count changed: header={count}, parsed={len(slices)}")
    ends = [offset + len(bundle) for offset, bundle in slices]
    starts = [header_size, *ends[:-1]]
    if [offset for offset, _ in slices] != starts:
        raise ValueError(".blob contains a gap or non-UnityFS bytes between bundles")
    if not ends or ends[-1] != len(data):
        raise ValueError(".blob final bundle does not reach EOF")
    output = bytearray(data)
    for index, end in enumerate(ends):
        if end > 0xFFFFFFFF:
            raise ValueError(f".blob bundle end exceeds 32-bit header field: {end}")
        output[8 + index * 4 : 12 + index * 4] = int(end).to_bytes(4, "little")
    return bytes(output)


def normalize_container(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def is_voice_clip(name: str, container: str) -> bool:
    return "/voice/" in container.lower() or bool(VOICE_TOKEN.search(name))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def fsbank_fingerprint(directory: Path) -> tuple[Any, ...]:
    """Return a stable identity for the native encoder used by the cache."""

    names = ("fsbank.dll", "fmod.dll", "libfsbvorbis64.dll", "fmodL.dll", "libmp3lame64.dll", "opus.dll")
    result: list[Any] = []
    for name in names:
        path = directory / name
        if path.is_file():
            stat = path.stat()
            result.append((name, stat.st_size, stat.st_mtime_ns, sha256_file(path)))
    return tuple(result)


def writable_cache_dir(preferred: Path) -> Path:
    """Create a usable cache directory, falling back to the system temp path."""

    candidates = [preferred, Path(tempfile.gettempdir()) / "MabinogiMobile-JPVoice-fsbank-cache"]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            candidate = candidate.expanduser().resolve()
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_bytes(b"ok")
            probe.unlink(missing_ok=True)
            return candidate
        except (OSError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f"FSBank cache directory is not writable: {last_error}")


def find_ffmpeg() -> Path:
    """Find the portable ffmpeg used for FSB5 Vorbis transcoding."""

    global _FFMPEG_PATH
    if _FFMPEG_PATH is not None:
        return _FFMPEG_PATH
    candidates = [
        Path(__file__).with_name("ffmpeg.exe"),
        Path(__file__).parent / "tools" / "ffmpeg.exe",
    ]
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(Path(on_path))
    try:
        import imageio_ffmpeg  # type: ignore

        candidates.append(Path(imageio_ffmpeg.get_ffmpeg_exe()))
    except Exception:
        pass
    for candidate in candidates:
        if candidate.is_file():
            _FFMPEG_PATH = candidate.resolve()
            return _FFMPEG_PATH
    raise RuntimeError(
        "需要 ffmpeg 才能把超出原资源槽的语音重新压缩。"
        "请把随包的 ffmpeg.exe 放在脚本同目录，或安装 imageio-ffmpeg。"
    )


def find_vgmstream() -> Path:
    """Find the bundled vgmstream decoder used for uncommon FSB5 headers."""

    global _VGMSTREAM_PATH
    if _VGMSTREAM_PATH is not None:
        return _VGMSTREAM_PATH
    candidates = [
        Path(__file__).parent / "vgmstream" / "vgmstream-cli.exe",
        Path(__file__).parent / "tools" / "vgmstream" / "vgmstream-cli.exe",
    ]
    on_path = shutil.which("vgmstream-cli")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file():
            _VGMSTREAM_PATH = candidate.resolve()
            return _VGMSTREAM_PATH
    raise FileNotFoundError("找不到 vgmstream-cli.exe")


def find_fsbank_dir(explicit: Path | None = None) -> Path:
    """Locate a directory containing the native FSBank encoder DLLs."""

    global _FSBANK_DIR
    if explicit is None and _FSBANK_DIR is not None:
        return _FSBANK_DIR
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    env_value = os.environ.get("MABINOGI_FSBANK_DIR", "").strip()
    if env_value:
        candidates.append(Path(env_value))
    package_root = Path(__file__).resolve().parent
    candidates.extend(
        [
            package_root / "fsbank",
            package_root / "tools" / "fsbank",
            package_root.parent.parent / "external" / "FSB5.Converter-research" / "FSBank" / "lib" / "x64",
            package_root.parent.parent.parent / "external" / "FSB5.Converter-research" / "FSBank" / "lib" / "x64",
        ]
    )
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        required = (candidate / "fsbank.dll", candidate / "fmod.dll", candidate / "libfsbvorbis64.dll")
        if all(path.is_file() for path in required):
            _FSBANK_DIR = candidate
            return candidate
    searched = ", ".join(str(path) for path in seen)
    raise FileNotFoundError(
        "严格模式需要原生 FSBank 编码器（fsbank.dll、fmod.dll、libfsbvorbis64.dll）。"
        "请使用 --fsbank-dir 或设置 MABINOGI_FSBANK_DIR；已搜索：" + searched
    )


def load_fsbank_bridge() -> Any:
    """Load the small ctypes bridge without requiring it as a pip package."""

    global _FSBANK_BRIDGE
    if _FSBANK_BRIDGE is not None:
        return _FSBANK_BRIDGE
    candidates = [
        Path(__file__).parent / "tools" / "fsbank_bridge.py",
        Path(__file__).parent / "fsbank_bridge.py",
        Path(__file__).resolve().parents[2] / "tools" / "fsbank_bridge.py",
    ]
    for bridge_path in candidates:
        if not bridge_path.is_file():
            continue
        import importlib.util

        spec = importlib.util.spec_from_file_location("mabinogi_fsbank_bridge", bridge_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _FSBANK_BRIDGE = module
        return module
    raise FileNotFoundError(
        "找不到 fsbank_bridge.py。请将该桥接文件与 fsbank_encode.py 放入包内 tools 目录。"
    )


def run_ffmpeg(args: list[str], input_bytes: bytes) -> bytes:
    """Run ffmpeg without opening a console window and return stdout."""

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [str(find_ffmpeg()), "-hide_banner", "-loglevel", "error", *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg 失败（退出码 {completed.returncode}）：{detail[:500]}")
    return bytes(completed.stdout)


def decode_fsb5_pcm(data: bytes) -> tuple[bytes, int, int]:
    """Decode FSB5 through vgmstream to signed 16-bit PCM."""

    executable = find_vgmstream()
    with tempfile.TemporaryDirectory(prefix="mabinogi-fsb5-") as temp_dir:
        source = Path(temp_dir) / "source.fsb"
        source.write_bytes(data)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [str(executable), "-p", "-i", str(source)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            creationflags=creationflags,
            cwd=str(executable.parent),
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"vgmstream 解码 FSB5 失败（退出码 {completed.returncode}）：{detail[:500]}")
        try:
            with wave.open(io.BytesIO(completed.stdout), "rb") as wav_file:
                channels = wav_file.getnchannels()
                frequency = wav_file.getframerate()
                width = wav_file.getsampwidth()
                pcm = wav_file.readframes(wav_file.getnframes())
        except (wave.Error, EOFError) as exc:
            raise RuntimeError("vgmstream 没有输出有效的 PCM WAV") from exc
        if width != 2:
            raise RuntimeError(f"vgmstream 输出了非 16 位 PCM（{width * 8} bit）")
        return pcm, channels, frequency


def validate_fsb5_payload(
    data: bytes,
    expected_duration: float,
    expected_channels: int | None = None,
    expected_frequency: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Check FSB metadata against a complete vgmstream decode."""

    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError(f"FSB5 payload exceeds capacity ({len(data)} > {max_bytes})")
    info = parse_fsb5(data)
    cache_key = (sha256_bytes(data), expected_duration, expected_channels, expected_frequency, max_bytes)
    if cache_key in _VALIDATED_PAYLOADS:
        return dict(_VALIDATED_PAYLOADS[cache_key])
    pcm, channels, frequency = decode_fsb5_pcm(data)
    if not pcm or channels <= 0 or frequency <= 0 or len(pcm) % (2 * channels):
        raise ValueError("vgmstream returned an invalid PCM frame layout")
    frames = len(pcm) // (2 * channels)
    if channels != info["channels"] or frequency != info["frequency"]:
        raise ValueError(
            "vgmstream output does not match FSB5 metadata "
            f"({channels}ch/{frequency}Hz vs {info['channels']}ch/{info['frequency']}Hz)"
        )
    if frames != info["sample_count"]:
        raise ValueError(
            f"vgmstream decoded {frames} frames, FSB5 header declares {info['sample_count']}"
        )
    if expected_channels is not None and channels != expected_channels:
        raise ValueError(f"decoded channel count {channels} does not match AudioClip metadata {expected_channels}")
    if expected_frequency is not None and frequency != expected_frequency:
        raise ValueError(f"decoded frequency {frequency} does not match AudioClip metadata {expected_frequency}")
    duration = frames / frequency
    if abs(duration - expected_duration) > (1.0 / frequency) + 1e-6:
        raise ValueError(
            f"decoded full duration differs from source ({duration:.6f}s vs {expected_duration:.6f}s)"
        )
    result = {
        "channels": channels,
        "frequency": frequency,
        "frames": frames,
        "duration": duration,
        "bytes": len(data),
    }
    _VALIDATED_PAYLOADS[cache_key] = dict(result)
    return result


def ogg_packets(data: bytes) -> list[bytes]:
    """Read complete packets from an Ogg stream."""

    packets: list[bytes] = []
    current = bytearray()
    cursor = 0
    while cursor < len(data):
        if data[cursor : cursor + 4] != b"OggS" or cursor + 27 > len(data):
            raise ValueError("ffmpeg 输出不是有效的 Ogg Vorbis 数据")
        segment_count = data[cursor + 26]
        table_end = cursor + 27 + segment_count
        if table_end > len(data):
            raise ValueError("Ogg 页头被截断")
        segments = data[cursor + 27 : table_end]
        body_end = table_end + sum(segments)
        if body_end > len(data):
            raise ValueError("Ogg 数据页被截断")
        body = data[table_end:body_end]
        body_cursor = 0
        for segment_size in segments:
            current.extend(body[body_cursor : body_cursor + segment_size])
            body_cursor += segment_size
            if segment_size < 255:
                packets.append(bytes(current))
                current.clear()
        cursor = body_end
    if current:
        raise ValueError("Ogg 最后一个数据包不完整")
    if len(packets) < 3:
        raise ValueError("Ogg Vorbis 缺少三个头部数据包")
    return packets


def ogg_crc(data: bytes) -> int:
    """Calculate the non-reflected CRC used by Ogg pages."""

    value = 0
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if value & 0x80000000 else (value << 1) & 0xFFFFFFFF
    return value


def make_ogg_page(packet: bytes, sequence: int, header_type: int, granule: int, serial: int = 1) -> bytes:
    """Put one Vorbis packet into a valid Ogg page."""

    if len(packet) > 255 * 255:
        raise ValueError("Vorbis packet 超过 Ogg 页容量")
    segments = [min(255, len(packet) - offset) for offset in range(0, len(packet), 255)]
    if not segments or len(packet) % 255 == 0:
        segments.append(0)
    page = bytearray(struct.pack("<4sBBQIIIB", b"OggS", 0, header_type, granule, serial, sequence, 0, len(segments)))
    page.extend(segments)
    page.extend(packet)
    struct.pack_into("<I", page, 22, ogg_crc(page))
    return bytes(page)


def parse_fsb5(data: bytes) -> dict[str, Any]:
    """Parse the single-sample Vorbis and IMA ADPCM forms used by the game."""

    if len(data) < 96 or data[:4] != b"FSB5":
        raise ValueError("资源不是 FSB5")
    _, version, sample_count, sample_headers_size, name_table_size, data_size, mode = struct.unpack_from(
        "<4sIIIIII", data, 0
    )
    header_size = 60 + (4 if version == 0 else 0)
    if version not in (0, 1) or sample_count != 1:
        raise ValueError(f"FSB5 样本数量不受支持（{sample_count}；严格模式只支持单样本）")
    if mode not in (7, 15):
        raise ValueError(f"FSB5 编码模式 {mode} 不受支持；当前支持 IMA ADPCM 和 Vorbis")
    sample_header_start = header_size
    sample_header_end = sample_header_start + sample_headers_size
    data_start = sample_header_end + name_table_size
    if sample_header_end > len(data) or data_start + data_size > len(data):
        raise ValueError("FSB5 资源长度字段越界")
    packed = struct.unpack_from("<Q", data, sample_header_start)[0]
    frequency_code = (packed >> 1) & 0xF
    channels = ((packed >> 5) & 1) + 1
    sample_count_pcm = (packed >> 34) & ((1 << 30) - 1)
    chunks: dict[int, tuple[int, int]] = {}
    cursor = sample_header_start + 8
    while cursor + 4 <= sample_header_end:
        raw = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        has_next = raw & 1
        size = (raw >> 1) & 0xFFFFFF
        chunk_type = (raw >> 25) & 0x7F
        if cursor + size > sample_header_end:
            raise ValueError("FSB5 样本元数据越界")
        chunks[chunk_type] = (cursor, size)
        cursor += size
        if not has_next:
            break
    vorbis_chunk = chunks.get(11)
    if mode == 15 and (vorbis_chunk is None or vorbis_chunk[1] < 4):
        raise ValueError("FSB5 缺少 VORBISDATA 元数据")
    crc_offset, crc_size = vorbis_chunk if vorbis_chunk is not None else (0, 0)
    crc = struct.unpack_from("<I", data, crc_offset)[0] if mode == 15 else None
    frequency = {1: 8000, 2: 11000, 3: 11025, 4: 16000, 5: 22050, 6: 24000, 7: 32000, 8: 44100, 9: 48000}.get(frequency_code)
    frequency_chunk = chunks.get(2)
    if frequency_chunk and frequency_chunk[1] >= 4:
        frequency = struct.unpack_from("<I", data, frequency_chunk[0])[0]
    if not frequency:
        raise ValueError("FSB5 缺少有效采样率")
    offset_units = (packed >> 6) & ((1 << 28) - 1)
    payload_start = data_start + offset_units * 16
    payload_end = data_start + data_size
    if payload_start > payload_end:
        raise ValueError("FSB5 音频偏移越界")
    return {
        "mode": mode,
        "version": version,
        "header_size": header_size,
        "sample_header_start": sample_header_start,
        "sample_header_end": sample_header_end,
        "data_start": data_start,
        "data_size": data_size,
        "payload_start": payload_start,
        "payload_end": payload_end,
        "packed": packed,
        "channels": channels,
        "frequency": frequency,
        "sample_count": sample_count_pcm,
        "crc": crc,
        "crc_offset": crc_offset,
        "vorbis_end": crc_offset + crc_size,
        "audio_data": data[payload_start:payload_end],
    }


def _silence_pcm(channels: int, frequency: int) -> bytes:
    # 0.1 seconds is enough for ffmpeg to emit all three Vorbis headers.
    return b"\0" * max(2 * channels * frequency // 10, 2 * channels)


def vorbis_headers_for_crc(channels: int, frequency: int, expected_crc: int) -> list[bytes]:
    """Find a Vorbis setup header by CRC.

    FSB5 stores playback frequency in a separate chunk.  The setup/codebook
    can still come from the encoder's nominal frequency (the replacement path
    deliberately uses the 44.1 kHz libvorbis setup for low-rate speech), so
    probe the playback rate first and then the known encoder rates.
    """

    encoder_rates = list(dict.fromkeys(
        [frequency, 44100, 32000, 22050, 16000, 11025, 8000]
    ))
    # libvorbis uses a quantised quality table.  Tenths cover the intermediate
    # presets used by FSB5 (including the common quality-54 stereo resources).
    for encoder_frequency in encoder_rates:
        key = (channels, encoder_frequency)
        cache = _VORBIS_HEADER_CACHE.setdefault(key, {})
        if expected_crc in cache:
            return cache[expected_crc][0]
        for index in range(0, 101):
            quality = index / 10.0
            encoded = run_ffmpeg(
                [
                    "-f", "s16le", "-ar", str(encoder_frequency), "-ac", str(channels), "-i", "pipe:0",
                    "-c:a", "libvorbis", "-q:a", f"{quality:.1f}", "-f", "ogg", "pipe:1",
                ],
                _silence_pcm(channels, encoder_frequency),
            )
            packets = ogg_packets(encoded)
            crc = zlib.crc32(packets[2]) & 0xFFFFFFFF
            cache.setdefault(crc, (packets[:3], quality))
            if crc == expected_crc:
                return packets[:3]
    raise ValueError(f"找不到与 FSB5 Vorbis CRC 0x{expected_crc:08x} 匹配的 Vorbis 头")


def fsb5_to_ogg(data: bytes) -> bytes:
    """Recreate a decodable Ogg stream from an FSB5 packet stream."""

    info = parse_fsb5(data)
    headers = vorbis_headers_for_crc(info["channels"], info["frequency"], info["crc"])
    packets: list[bytes] = []
    cursor = 0
    payload = info["audio_data"]
    while cursor + 2 <= len(payload):
        packet_size = struct.unpack_from("<H", payload, cursor)[0]
        cursor += 2
        if packet_size == 0:
            break
        if cursor + packet_size > len(payload):
            raise ValueError("FSB5 Vorbis 数据包越界")
        packets.append(payload[cursor : cursor + packet_size])
        cursor += packet_size
    if not packets:
        raise ValueError("FSB5 没有音频数据包")
    output = bytearray()
    for sequence, header in enumerate(headers):
        output.extend(make_ogg_page(header, sequence, 2 if sequence == 0 else 0, 0))
    for index, packet in enumerate(packets):
        granule = int(info["sample_count"] * (index + 1) / len(packets))
        output.extend(make_ogg_page(packet, len(headers) + index, 4 if index == len(packets) - 1 else 0, granule))
    return bytes(output)


def retarget_fsb5_template(data: bytes, channels: int, frequency: int) -> bytes:
    """Adjust the compact FSB5 sample header for a fallback sample rate/channel count."""

    codes = {8000: 1, 11000: 2, 11025: 3, 16000: 4, 22050: 5, 24000: 6, 32000: 7, 44100: 8, 48000: 9}
    if frequency not in codes or channels not in (1, 2):
        raise ValueError(f"FSB5 不支持的重采样格式：{channels}ch/{frequency}Hz")
    info = parse_fsb5(data)
    sample_header = bytearray(data[info["sample_header_start"] : info["sample_header_end"]])
    packed = info["packed"]
    packed = (packed & ~(0xF << 1)) | (codes[frequency] << 1)
    packed = (packed & ~(1 << 5)) | ((channels - 1) << 5)
    struct.pack_into("<Q", sample_header, 0, packed)
    relative_crc = info["crc_offset"] - info["sample_header_start"]
    return data[: info["header_size"]] + bytes(sample_header) + data[info["sample_header_end"] : info["payload_start"]] + info["audio_data"]


def resample_pcm(pcm: bytes, in_channels: int, in_frequency: int, out_channels: int, out_frequency: int) -> bytes:
    if (in_channels, in_frequency) == (out_channels, out_frequency):
        return pcm
    return run_ffmpeg(
        [
            "-f", "s16le", "-ar", str(in_frequency), "-ac", str(in_channels), "-i", "pipe:0",
            "-f", "s16le", "-ar", str(out_frequency), "-ac", str(out_channels), "pipe:1",
        ],
        pcm,
    )


def encode_fsb5_at_quality(
    ogg_pcm: bytes,
    channels: int,
    frequency: int,
    quality: float,
    template: bytes,
    *,
    encoder_frequency: int = 44100,
) -> bytes:
    """Encode PCM as a self-consistent FSB5 Vorbis sample.

    The Vorbis setup belongs to the encoded packet stream.  Reusing the
    template CRC after changing the encoder settings can produce a file that
    third-party decoders accept but FMOD decodes incorrectly, so the setup CRC
    and explicit frequency chunk are rebuilt from the actual Ogg headers.
    """

    # FMOD's FSB5 setup/codebook is selected by the encoder's nominal rate,
    # while the explicit FSB frequency chunk controls playback.  The game
    # resources use the 44.1 kHz libvorbis setup for low-rate speech too.
    encoded = run_ffmpeg(
        [
            "-f", "s16le", "-ar", str(encoder_frequency), "-ac", str(channels), "-i", "pipe:0",
            "-c:a", "libvorbis", "-q:a", f"{quality:g}", "-f", "ogg", "pipe:1",
        ],
        ogg_pcm,
    )
    packets = ogg_packets(encoded)
    if len(packets) < 4:
        raise ValueError("ffmpeg 输出的 Vorbis 数据包不足")
    info = parse_fsb5(template)
    if info["payload_start"] != info["data_start"]:
        raise ValueError("仅支持单样本且音频数据从资源起点开始的 FSB5")
    custom = bytearray()
    for packet in packets[3:]:
        if not 0 < len(packet) <= 65535:
            raise ValueError("Vorbis 数据包超出 FSB5 uint16 长度范围")
        custom.extend(struct.pack("<H", len(packet)))
        custom.extend(packet)
    custom.extend(b"\0\0")
    custom.extend(b"\0" * (-len(custom) % 16))
    info = parse_fsb5(template)
    header = bytearray(template[: info["header_size"]])
    struct.pack_into("<I", header, 20, len(custom))
    chunks: list[tuple[int, bytes]] = []
    cursor = info["sample_header_start"] + 8
    more = True
    while more:
        raw = struct.unpack_from("<I", template, cursor)[0]
        cursor += 4
        size = (raw >> 1) & 0xFFFFFF
        chunk_type = raw >> 25
        payload = template[cursor : cursor + size]
        cursor += size
        more = bool(raw & 1)
        if chunk_type == 11:
            payload = struct.pack("<II", zlib.crc32(packets[2]) & 0xFFFFFFFF, 0)
        elif chunk_type == 2:
            continue
        chunks.append((chunk_type, payload))
    chunks.append((2, struct.pack("<I", frequency)))
    sample_count = len(ogg_pcm) // (2 * channels)
    if sample_count >= 1 << 30:
        raise ValueError("FSB5 帧数过大")
    packed = (info["packed"] & ~(((1 << 30) - 1) << 34)) | (sample_count << 34)
    sample_header = bytearray(struct.pack("<Q", packed | 1))
    for index, (chunk_type, payload) in enumerate(chunks):
        if len(payload) >= (1 << 23):
            raise ValueError("FSB5 样本元数据块过大")
        sample_header.extend(struct.pack("<I", (chunk_type << 25) | (len(payload) << 1) |
                                          (index + 1 < len(chunks))))
        sample_header.extend(payload)
    sample_header.extend(b"\0" * (-len(sample_header) % 16))
    struct.pack_into("<I", header, 12, len(sample_header))
    return bytes(header) + bytes(sample_header) + template[info["sample_header_end"] : info["data_start"]] + bytes(custom)


def trim_zero_edges(pcm: bytes, channels: int, frequency: int) -> tuple[bytes, dict[str, Any]]:
    """Remove only exact-zero edge frames, retaining 10 ms beside the sound."""
    if not pcm or channels <= 0 or frequency <= 0 or len(pcm) % (2 * channels):
        raise ValueError("Invalid PCM layout for silence trimming")
    frame_bytes = 2 * channels
    frames = len(pcm) // frame_bytes
    zero = b"\0" * frame_bytes
    first, last = 0, frames
    while first < frames and pcm[first * frame_bytes:(first + 1) * frame_bytes] == zero:
        first += 1
    if first == frames:
        # Do not remove an entire clip, even when the original is silent.
        first = 0
    else:
        while last > first and pcm[(last - 1) * frame_bytes:last * frame_bytes] == zero:
            last -= 1
    padding = math.ceil(frequency * 0.010)
    start = max(0, first - padding)
    end = min(frames, last + padding)
    details = {
        "source_frequency": frequency, "source_frames": frames,
        "leading_frames": start, "trailing_frames": frames - end,
        "retained_frames": end - start,
        "original_duration": frames / frequency,
        "retained_duration": (end - start) / frequency,
        "leading_seconds": start / frequency,
        "trailing_seconds": (frames - end) / frequency,
        "silence_threshold_pcm": 0, "padding_ms": 10,
    }
    return pcm[start * frame_bytes:end * frame_bytes], details


def restore_resampled_gain(source: bytes, converted: bytes) -> tuple[bytes, float]:
    """Restore lost RMS gently, with a 6 dB gain and peak limit."""
    def samples(data: bytes) -> array:
        values = array("h")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
        return values
    original, reduced = samples(source), samples(converted)
    if not original or not reduced:
        raise ValueError("Cannot normalize empty PCM")
    rms_source = math.sqrt(sum(v * v for v in original) / len(original))
    rms_reduced = math.sqrt(sum(v * v for v in reduced) / len(reduced))
    peak = max(abs(v) for v in reduced)
    if not peak or not rms_reduced:
        return converted, 1.0
    gain = max(1.0, min(2.0, rms_source / rms_reduced, 31129.0 / peak))
    if gain == 1.0:
        return converted, gain
    output = array("h", (round(v * gain) for v in reduced))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes(), gain


@lru_cache(maxsize=1)
def load_ima_encoder() -> Any:
    import importlib.util
    paths = [Path(__file__).parent / "tools" / "ima_fsb_encoder.py", Path(__file__).parent / "ima_fsb_encoder.py"]
    for path in paths:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("mabinogi_ima_encoder", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError("Missing tools/ima_fsb_encoder.py")


def compress_fsb5_to_capacity(
    data: bytes,
    capacity: int,
    preferred_channels: int | None = None,
    preferred_frequency: int | None = None,
    fsbank_dir: Path | None = None,
    fsbank_cache_dir: Path | None = None,
) -> EncodedClip | None:
    """Fit complete speech, permitting only exact-zero edge trimming.

    Prefer native FSBank Vorbis; use standard game-supported IMA ADPCM for
    the tiny slots where Vorbis cannot fit. No voiced frame is removed and
    no tempo change or silent substitution is used.
    """

    fsbank_dir = find_fsbank_dir(fsbank_dir)
    global _FSBANK_CACHE_DIR
    if fsbank_cache_dir is not None:
        cache_dir = fsbank_cache_dir.expanduser().resolve()
    elif _FSBANK_CACHE_DIR is None:
        configured_cache = os.environ.get("MABINOGI_FSBANK_CACHE", "").strip()
        cache_dir = (
            Path(configured_cache).expanduser().resolve()
            if configured_cache
            else Path(__file__).parent / "tmp-fsbcache"
        )
        _FSBANK_CACHE_DIR = cache_dir
    else:
        cache_dir = _FSBANK_CACHE_DIR
    cache_dir = writable_cache_dir(cache_dir)
    bridge = load_fsbank_bridge()
    source_info = parse_fsb5(data)
    source_key = (
        REENCODE_STRATEGY_VERSION,
        str(fsbank_dir),
        fsbank_fingerprint(fsbank_dir),
        sha256_bytes(data),
        capacity,
        preferred_channels,
        preferred_frequency,
    )
    cached = _REENCODE_CACHE.get(source_key)
    if cached is not None:
        return cached
    pcm, decoded_channels, decoded_frequency = decode_fsb5_pcm(data)
    if decoded_channels != source_info["channels"] or decoded_frequency != source_info["frequency"]:
        raise RuntimeError("Source FSB5 metadata differs from the complete decode")
    source_duration = len(pcm) / (2 * decoded_channels * decoded_frequency)
    trimmed, trim = trim_zero_edges(pcm, decoded_channels, decoded_frequency)
    key_digest = sha256_bytes(json.dumps(source_key, ensure_ascii=True, sort_keys=True).encode())
    result_dir = cache_dir / "validated-results-v3"
    result_dir.mkdir(exist_ok=True)
    cached_meta = result_dir / f"{key_digest}.json"
    cached_fsb = result_dir / f"{key_digest}.fsb"
    if cached_meta.is_file() and cached_fsb.is_file():
        try:
            record = json.loads(cached_meta.read_text(encoding="utf-8"))
            if record.get("source_sha256") != sha256_bytes(data) or record.get("strategy") != REENCODE_STRATEGY_VERSION:
                raise ValueError("Cached result belongs to a different source or strategy")
            payload = cached_fsb.read_bytes()
            if sha256_bytes(payload) != record["sha256"]:
                raise ValueError("Cached encoding checksum mismatch")
            if abs(record["details"]["source_duration"] - source_duration) > 1e-9:
                raise ValueError("Cached source duration differs")
            cached_trim = record["details"].get("trim", {})
            if cached_trim and cached_trim != trim:
                raise ValueError("Cached trim does not match verified exact-zero source edges")
            retained_duration = record["details"].get("trim", {}).get("retained_duration", record["details"]["source_duration"])
            checked = validate_fsb5_payload(payload, retained_duration, record["channels"], record["frequency"], capacity)
            codec = parse_fsb5(payload)["mode"]
            if record["details"]["compression_format"] != (1 if codec == 15 else 2):
                raise ValueError("Cached codec metadata differs from its payload")
            result = EncodedClip(payload, record["quality"], record["channels"], record["frequency"], checked["duration"], record["encoder"], record["details"])
            _REENCODE_CACHE[source_key] = result
            return result
        except (OSError, ValueError, KeyError, TypeError):
            # A bad cache never permits a partial replacement; recompute it.
            pass
    supported_rates = (48000, 44100, 32000, 24000, 22050, 16000, 11025, 11000, 8000)
    rates = [
        rate for rate in (preferred_frequency, source_info["frequency"], *supported_rates)
        if rate in supported_rates
    ]
    rates = list(dict.fromkeys(rates))
    channels = [preferred_channels, source_info["channels"]]
    if source_info["channels"] == 2 or preferred_channels == 2:
        channels.append(1)
    channels = [value for value in dict.fromkeys(channels) if value in (1, 2)]
    # 0 means FSBank's default quality, not its lowest. Size is not assumed
    # monotone: inspect every preset, but fully decode only fitting candidates.
    qualities = list(range(100, 0, -10)) + [5, 1]
    variants = [(pcm, None)]
    if trimmed != pcm:
        variants.append((trimmed, trim))

    def save_result(result: EncodedClip) -> EncodedClip:
        _REENCODE_CACHE[source_key] = result
        record = {"sha256": sha256_bytes(result.payload), "source_sha256": sha256_bytes(data), "strategy": REENCODE_STRATEGY_VERSION, "quality": result.quality,
                  "channels": result.channels, "frequency": result.frequency,
                  "duration": result.duration, "encoder": result.encoder, "details": result.details}
        # Atomic files and the payload hash make interrupted cache writes safe.
        with tempfile.TemporaryDirectory(prefix="result-", dir=result_dir) as temp:
            temp_path = Path(temp)
            (temp_path / "payload").write_bytes(result.payload)
            (temp_path / "metadata").write_text(json.dumps(record, ensure_ascii=True), encoding="utf-8")
            os.replace(temp_path / "payload", cached_fsb)
            os.replace(temp_path / "metadata", cached_meta)
        return result

    def accept(candidate: bytes, quality: float | None, encoder: str, content: bytes,
               trim_details: dict[str, Any] | None, extra: dict[str, Any]) -> EncodedClip:
        info = parse_fsb5(candidate)
        expected_duration = len(content) / (2 * decoded_channels * decoded_frequency)
        checked = validate_fsb5_payload(candidate, expected_duration, info["channels"], info["frequency"], capacity)
        details = {"source_duration": source_duration, "compression_format": 1 if info["mode"] == 15 else 2,
                   "trim": trim_details or {}, **extra}
        return EncodedClip(candidate, quality, info["channels"], info["frequency"], checked["duration"], encoder, details)

    for content, trim_details in variants:
        for out_channels in channels:
            for requested_frequency in rates:
                converted = resample_pcm(content, decoded_channels, decoded_frequency, out_channels, requested_frequency)
                # Checking low presets first exposes difficult slots promptly.
                candidates: dict[int, bytes] = {}
                smallest = bridge.encode_pcm(converted, out_channels, requested_frequency, 1, fsbank_dir, cache_dir)
                candidates[1] = smallest
                # Never infer size from tool errors. Failed encode/decode raises
                # an explicit diagnostic instead of being treated as too large.
                if len(smallest) > capacity:
                    # Do not reject a format using an unproven size ordering.
                    # Scan the remaining finite grid even when q1 is oversized.
                    for q in qualities:
                        if q in candidates:
                            continue
                        candidates[q] = bridge.encode_pcm(converted, out_channels, requested_frequency, q, fsbank_dir, cache_dir)
                    if all(len(value) > capacity for value in candidates.values()):
                        continue
                # Scan the actual quality grid descending. This yields the best
                # validated preset in this grid, without a binary-search claim.
                for quality in qualities:
                    candidate = candidates.get(quality)
                    if candidate is None:
                        candidate = bridge.encode_pcm(converted, out_channels, requested_frequency, quality, fsbank_dir, cache_dir)
                    if len(candidate) <= capacity:
                        result = accept(candidate, float(quality), "fsbank", content, trim_details,
                                        {"input_frequency": requested_frequency, "gain": 1.0})
                        return save_result(result)

    # Standard Xbox/FSB IMA ADPCM already exists in the client's resources.
    # Determine its exact capacity mathematically; do not search codec quality
    # or discard speech to make a fixed-rate recording shorter.
    ima = load_ima_encoder()
    content, trim_details = variants[-1]
    retained_duration = len(content) / (2 * decoded_channels * decoded_frequency)
    low, high = 1, int(MIN_ACCEPTABLE_INPUT_FREQUENCY * retained_duration) + 2
    max_frames = 0
    while low <= high:
        middle = (low + high) // 2
        if ima.encoded_size(middle) <= capacity:
            max_frames = middle
            low = middle + 1
        else:
            high = middle - 1
    # A result below the quality floor is intentionally skipped instead of
    # being forced into the slot with an unintelligible narrow-band signal.
    rate = min(MIN_ACCEPTABLE_INPUT_FREQUENCY, int(max_frames / retained_duration)) if retained_duration else 0
    if rate < MIN_ACCEPTABLE_INPUT_FREQUENCY:
        return None
    converted = resample_pcm(content, decoded_channels, decoded_frequency, 1, rate)
    if ima.encoded_size(len(converted) // 2) > capacity:
        return None
    converted, gain = restore_resampled_gain(content, converted)
    candidate = ima.encode_pcm(converted, 1, rate)
    result = accept(candidate, None, "ima-adpcm", content, trim_details,
                    {"input_frequency": rate, "gain": gain, "gain_db": 20 * math.log10(gain)})
    return save_result(result)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def resource_basename(source: Any) -> str:
    return str(source or "").replace("\\", "/").rsplit("/", 1)[-1]


def aligned(value: int) -> int:
    return (value + ALIGNMENT - 1) // ALIGNMENT * ALIGNMENT


def clip_signature(clip: Any) -> dict[str, Any]:
    resource = getattr(clip, "m_Resource", None)
    return {
        "channels": getattr(clip, "m_Channels", None),
        "frequency": getattr(clip, "m_Frequency", None),
        "bits_per_sample": getattr(clip, "m_BitsPerSample", None),
        "compression_format": getattr(clip, "m_CompressionFormat", None),
        "load_type": getattr(clip, "m_LoadType", None),
        "stream": getattr(clip, "m_Stream", None),
        "has_external_resource": bool(resource and getattr(resource, "m_Source", "")),
    }


def validate_already_identical(
    clip: Any,
    jp: JPClip,
    resource_bytes: bytes,
    capacity: int,
    validator: Any = None,
) -> dict[str, Any] | None:
    """Verify an exact resource/metadata match without requiring any rewrite."""
    length = float(getattr(clip, "m_Length", 0.0) or 0.0)
    if (
        resource_bytes != jp.resource_bytes
        or clip_signature(clip) != jp.signature
        or not math.isfinite(length)
        or length != jp.length
    ):
        return None
    validate = validator or validate_fsb5_payload
    return validate(
        resource_bytes,
        expected_duration=jp.length,
        expected_channels=jp.signature.get("channels"),
        expected_frequency=jp.signature.get("frequency"),
        max_bytes=capacity,
    )


def validate_fsb5_payload_fmod(
    data: bytes,
    expected_duration: float,
    expected_channels: int | None = None,
    expected_frequency: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Verify unchanged full-rebuild resources with UnityPy's existing decoder."""
    import fmod_toolkit

    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError(f"FSB5 payload exceeds capacity ({len(data)} > {max_bytes})")
    info = parse_fsb5(data)
    cache_key = ("fmod", sha256_bytes(data), expected_duration, expected_channels, expected_frequency, max_bytes)
    if cache_key in _VALIDATED_PAYLOADS:
        return dict(_VALIDATED_PAYLOADS[cache_key])
    samples = fmod_toolkit.raw_to_wav(data, "voice-validation", info["channels"], info["frequency"])
    if len(samples) != 1:
        raise ValueError("FMOD did not return exactly one decoded AudioClip")
    with wave.open(io.BytesIO(next(iter(samples.values()))), "rb") as decoded:
        channels = decoded.getnchannels()
        frequency = decoded.getframerate()
        frames = decoded.getnframes()
        pcm = decoded.readframes(frames)
        if decoded.getsampwidth() != 2 or not pcm or len(pcm) != frames * channels * 2:
            raise ValueError("FMOD returned an incomplete 16-bit PCM frame layout")
    if channels != info["channels"] or frequency != info["frequency"]:
        raise ValueError("FMOD output does not match FSB5 channel/frequency metadata")
    if frames != info["sample_count"]:
        raise ValueError(f"FMOD decoded {frames} frames, FSB5 header declares {info['sample_count']}")
    if expected_channels is not None and channels != expected_channels:
        raise ValueError("decoded channel count does not match AudioClip metadata")
    if expected_frequency is not None and frequency != expected_frequency:
        raise ValueError("decoded frequency does not match AudioClip metadata")
    duration = frames / frequency
    if not math.isfinite(expected_duration) or abs(duration - expected_duration) > (1.0 / frequency) + 1e-6:
        raise ValueError(f"decoded full duration differs from source ({duration:.6f}s vs {expected_duration:.6f}s)")
    result = {"channels": channels, "frequency": frequency, "frames": frames, "duration": duration, "bytes": len(data)}
    _VALIDATED_PAYLOADS[cache_key] = dict(result)
    return result


@dataclass
class JPClip:
    name: str
    container: str
    length: float
    signature: dict[str, Any]
    resource_bytes: bytes
    resource_name: str
    source_blob: str
    source_offset: int
    path_id: int


@dataclass
class TWClip:
    name: str
    container: str
    signature: dict[str, Any]
    blob_path: Path
    bundle_offset: int
    path_id: int
    resource_size: int = 0


def load_unitypy() -> Any:
    try:
        import UnityPy  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "UnityPy is required. Run: python -m pip install -r requirements.txt"
        ) from exc
    return UnityPy


def load_bundle(UnityPy: Any, data: bytes) -> Any:
    return UnityPy.load(data)


def load_resource_cache(cache_dir: Path) -> tuple[dict[tuple[str, str], JPClip], dict[str, list[JPClip]], dict[str, Any]]:
    """Load a package-local cache of original JP compressed resource bytes."""

    metadata_path = cache_dir / "resource_cache.json"
    data_path = cache_dir / "resource_cache.bin"
    if not metadata_path.is_file() or not data_path.is_file():
        raise ValueError(
            f"Bundled resource cache is missing: expected {metadata_path.name} and {data_path.name}"
        )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("format") != 1 or metadata.get("source") != "MabinogiMobile_JP":
        raise ValueError("Unsupported bundled resource cache format/source")
    raw = data_path.read_bytes()
    items = metadata.get("clips")
    if not isinstance(items, list) or not items:
        raise ValueError("Bundled resource cache contains no clips")
    if metadata.get("clip_count") != len(items) or metadata.get("payload_bytes") != len(raw):
        raise ValueError("Bundled resource cache count/size mismatch")
    by_full: dict[tuple[str, str], JPClip] = {}
    by_name: dict[str, list[JPClip]] = defaultdict(list)
    for item in items:
        offset = int(item["offset"])
        size = int(item["size"])
        if offset < 0 or size <= 0 or offset + size > len(raw):
            raise ValueError(f"Invalid bundled resource range for {item.get('name', '<unnamed>')}")
        payload = raw[offset : offset + size]
        if len(payload) != size or sha256_bytes(payload) != item.get("sha256"):
            raise ValueError(f"Bundled resource cache failed checksum for {item.get('name', '<unnamed>')}")
        record = JPClip(
            name=str(item["name"]),
            container=normalize_container(item.get("container", "")),
            length=float(item.get("length", 0.0)),
            signature=dict(item.get("signature", {})),
            resource_bytes=payload,
            resource_name=str(item.get("resource_name", "")),
            source_blob=str(item.get("source_blob", "bundled-cache")),
            source_offset=int(item.get("source_offset", 0)),
            path_id=int(item.get("path_id", 0)),
        )
        key = (record.name.casefold(), record.container)
        if key in by_full or not record.name or not math.isfinite(record.length) or record.length <= 0:
            raise ValueError(f"Invalid or duplicate bundled clip: {record.name}")
        by_full[key] = record
        by_name[record.name.casefold()].append(record)
    stats = {
        "cache_dir": str(cache_dir),
        "usable_clip_count": len(by_full),
        "cache_bytes": len(raw),
        "metadata_sha256": sha256_file(metadata_path),
        "payload_sha256": sha256_bytes(raw),
        "all_clip_checksums_passed": True,
        "engine_versions": list(metadata.get("engine_versions", [])),
    }
    return by_full, by_name, stats


def resource_cache_value(
    by_full: dict[tuple[str, str], JPClip],
    engine_versions: list[str] | set[str],
    cache_key: str,
) -> tuple[bytes, dict[str, Any]]:
    """Serialize selected JP clips for reuse without modifying the bundled cache."""

    payload = bytearray()
    records: list[dict[str, Any]] = []
    for record in sorted(by_full.values(), key=lambda item: (item.name.casefold(), item.container)):
        offset = len(payload)
        payload.extend(record.resource_bytes)
        records.append({
            "name": record.name,
            "container": record.container,
            "length": record.length,
            "signature": record.signature,
            "resource_name": record.resource_name,
            "source_blob": record.source_blob,
            "source_offset": record.source_offset,
            "path_id": record.path_id,
            "offset": offset,
            "size": len(record.resource_bytes),
            "sha256": sha256_bytes(record.resource_bytes),
        })
    metadata = {
        "format": 1,
        "source": "MabinogiMobile_JP",
        "cache_key": cache_key,
        "generated_utc": now_utc(),
        "engine_versions": sorted(engine_versions),
        "clip_count": len(records),
        "payload_bytes": len(payload),
        "clips": records,
    }
    return bytes(payload), metadata


def load_selected_jp_cache(cache_dir: Path, cache_key: str) -> tuple[dict[tuple[str, str], JPClip], dict[str, list[JPClip]], dict[str, Any]] | None:
    """Load a JP scan cache only when its source fingerprint and target set match."""

    metadata_path = cache_dir / "resource_cache.json"
    data_path = cache_dir / "resource_cache.bin"
    if not metadata_path.is_file() or not data_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("cache_key") != cache_key:
            return None
        by_full, by_name, stats = load_resource_cache(cache_dir)
        stats["selected_cache"] = True
        stats["cache_key"] = cache_key
        return by_full, by_name, stats
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def save_selected_jp_cache(
    cache_dir: Path,
    by_full: dict[tuple[str, str], JPClip],
    engine_versions: list[str] | set[str],
    cache_key: str,
) -> None:
    """Atomically save the selected JP scan result beside the tool."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    payload, metadata = resource_cache_value(by_full, engine_versions, cache_key)
    with tempfile.TemporaryDirectory(prefix="jp-cache-", dir=cache_dir.parent) as temporary:
        temp = Path(temporary)
        (temp / "resource_cache.bin").write_bytes(payload)
        (temp / "resource_cache.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temp / "resource_cache.bin", cache_dir / "resource_cache.bin")
        os.replace(temp / "resource_cache.json", cache_dir / "resource_cache.json")


def client_data_root(path: Path, label: str) -> tuple[Path, Path]:
    """Return the game root and its package/data root.

    The game normally stores AssetPackages below ``appdata``.  Older tool
    versions asked for the inner directory, which made it easy to create a
    copy with package folders beside ``appdata``.  Accept both input forms,
    but keep the outer game root for copying so the layout is preserved.
    """
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{label} does not exist or is not a directory: {path}")
    has_packages = any((path / dirname).is_dir() for dirname in PACKAGE_DIRS)
    nested = path / "appdata"
    nested_has_packages = nested.is_dir() and any(
        (nested / dirname).is_dir() for dirname in PACKAGE_DIRS
    )
    if nested_has_packages:
        return path, nested
    if has_packages:
        return (path.parent if path.name.casefold() == "appdata" else path), path
    raise ValueError(
        f"{label} must be a game root containing appdata or an appdata directory "
        f"containing AssetPackages/ResourcesPackage: {path}"
    )


def validate_root(root: Path, label: str) -> None:
    if not root.is_dir():
        raise ValueError(f"{label} does not exist or is not a directory: {root}")
    blobs = list(iter_blob_files(root))
    if not blobs:
        raise ValueError(f"No .blob files found below {label}: {root}")


def manifest_info(root: Path) -> list[dict[str, Any]]:
    candidates = sorted(
        p for p in root.iterdir() if p.is_file() and "manifest" in p.name.lower()
    )
    return [
        {"path": str(p.relative_to(root)).replace("\\", "/"), "bytes": p.stat().st_size, "sha256": sha256_file(p)}
        for p in candidates
    ]


def assert_not_running() -> None:
    """Refuse a write while the game/launcher may have a package open."""

    if os.name != "nt":
        return
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
        )
    except OSError:
        return
    names = {"mabinogimobile.exe", "ngm64.exe", "blackcipher.aes", "blackcipher.exe"}
    running: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith('"'):
            process_name = line.split('"', 2)[1].lower()
            if process_name in names:
                running.add(process_name)
    if running:
        raise RuntimeError(
            "Close the game and launcher before writing a copy; running processes: "
            + ", ".join(sorted(running))
        )


def scan_jp(
    root: Path,
    UnityPy: Any,
    progress: bool = True,
    wanted_names: set[str] | None = None,
) -> tuple[dict[tuple[str, str], JPClip], dict[str, list[JPClip]], dict[str, Any]]:
    by_full: dict[tuple[str, str], JPClip] = {}
    by_name: dict[str, list[JPClip]] = defaultdict(list)
    blobs = list(iter_blob_files(root))
    emit_progress("scan_jp", 0, len(blobs), unit="files", enabled=progress)
    scanned = 0
    bundles = 0
    audio_objects = 0
    skipped = 0
    engines: set[str] = set()
    wanted_names = {name.casefold() for name in (wanted_names or set())}
    for index, blob_path in enumerate(blobs, 1):
        scanned = index
        blob_data = blob_path.read_bytes()
        relative_blob = str(blob_path.relative_to(root)).replace("\\", "/")
        for bundle_offset, bundle_data in iter_unityfs_slices(blob_data):
            try:
                env = load_bundle(UnityPy, bundle_data)
            except Exception as exc:
                skipped += 1
                print(f"WARN JP bundle parse failed {relative_blob}@{bundle_offset}: {exc}", file=sys.stderr)
                continue
            bundles += 1
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
                    if wanted_names and name.casefold() not in wanted_names:
                        continue
                    audio_objects += 1
                    resource = getattr(clip, "m_Resource", None)
                    source = getattr(resource, "m_Source", "") if resource else ""
                    resource_name = resource_basename(source)
                    if not resource_name or resource_name not in files:
                        skipped += 1
                        continue
                    offset = int(getattr(resource, "m_Offset", 0) or 0)
                    size = int(getattr(resource, "m_Size", 0) or 0)
                    raw_resource = bytes(files[resource_name].bytes)
                    if offset < 0 or size <= 0 or offset + size > len(raw_resource):
                        skipped += 1
                        continue
                    record = JPClip(
                        name=name,
                        container=container,
                        length=float(getattr(clip, "m_Length", 0.0) or 0.0),
                        signature=clip_signature(clip),
                        resource_bytes=raw_resource[offset : offset + size],
                        resource_name=resource_name,
                        source_blob=relative_blob,
                        source_offset=bundle_offset,
                        path_id=int(obj.path_id),
                    )
                    full_key = (name.casefold(), container)
                    previous = by_full.get(full_key)
                    if previous is None or len(record.resource_bytes) > len(previous.resource_bytes):
                        by_full[full_key] = record
                        same_name = by_name[name.casefold()]
                        replaced = False
                        for duplicate_index, duplicate in enumerate(same_name):
                            if duplicate.container == container:
                                same_name[duplicate_index] = record
                                replaced = True
                                break
                        if not replaced:
                            same_name.append(record)
                except Exception as exc:
                    skipped += 1
                    print(f"WARN JP AudioClip read failed {relative_blob}@{bundle_offset}: {exc}", file=sys.stderr)
        if progress and (index == 1 or index % 25 == 0 or index == len(blobs)):
            emit_progress("scan_jp", index, len(blobs), unit="files", enabled=progress)
            print(f"JP [{index}/{len(blobs)}] clips={len(by_full)} bundles={bundles}", flush=True)
        if wanted_names and wanted_names.issubset(by_name):
            if progress:
                print(f"JP target names found after {index}/{len(blobs)} blobs", flush=True)
            break
    emit_progress("scan_jp", scanned, len(blobs), unit="files", complete=True, enabled=progress)
    stats = {
        "blob_count": len(blobs),
        "bundle_count": bundles,
        "audio_object_count": audio_objects,
        "usable_clip_count": len(by_full),
        "skipped_count": skipped,
        "engine_versions": sorted(engines),
    }
    return by_full, by_name, stats


def scan_tw_targets(
    root: Path,
    UnityPy: Any,
    include: list[re.Pattern[str]],
    max_clips: int,
    progress: bool = True,
    priority: re.Pattern[str] | None = None,
    blob_filter: set[str] | None = None,
) -> tuple[list[TWClip], dict[str, Any]]:
    """Index TW voice objects, optionally prioritising a small test profile."""

    targets: list[TWClip] = []
    priority_targets: list[TWClip] = []
    fallback_targets: list[TWClip] = []
    blobs = list(iter_blob_files(root))
    emit_progress("scan_tw", 0, len(blobs), unit="files", enabled=progress)
    scanned = 0
    bundles = 0
    audio_objects = 0
    engines: set[str] = set()
    for index, blob_path in enumerate(blobs, 1):
        scanned = index
        relative_blob = str(blob_path.relative_to(root)).replace("\\", "/")
        if blob_filter is not None and relative_blob.casefold() not in blob_filter:
            continue
        blob_data = blob_path.read_bytes()
        for bundle_offset, bundle_data in iter_unityfs_slices(blob_data):
            try:
                env = load_bundle(UnityPy, bundle_data)
            except Exception as exc:
                print(f"WARN TW bundle parse failed {relative_blob}@{bundle_offset}: {exc}", file=sys.stderr)
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
                    audio_objects += 1
                    if include and not any(pattern.search(name) or pattern.search(container) for pattern in include):
                        continue
                    resource = getattr(clip, "m_Resource", None)
                    target = TWClip(
                        name, container, clip_signature(clip), blob_path, bundle_offset,
                        int(obj.path_id), int(getattr(resource, "m_Size", 0) or 0),
                    )
                    if priority and priority.search(name):
                        priority_targets.append(target)
                    else:
                        fallback_targets.append(target)
                    targets.append(target)
                    if priority and max_clips > 0 and len(priority_targets) >= max_clips:
                        selected = priority_targets[:max_clips]
                        stats = {
                            "blob_count": len(blobs),
                            "bundle_count": bundles,
                            "audio_object_count": audio_objects,
                            "selected_target_count": len(selected),
                            "priority_pattern": priority.pattern,
                            "priority_target_count": len(priority_targets),
                            "engine_versions": sorted(engines),
                        }
                        emit_progress("scan_tw", index, len(blobs), unit="files", complete=True, enabled=progress)
                        return selected, stats
                    if max_clips > 0 and priority is None and len(targets) >= max_clips:
                        stats = {"blob_count": len(blobs), "bundle_count": bundles, "audio_object_count": audio_objects, "selected_target_count": len(targets), "engine_versions": sorted(engines)}
                        emit_progress("scan_tw", index, len(blobs), unit="files", complete=True, enabled=progress)
                        return targets, stats
                except Exception as exc:
                    print(f"WARN TW AudioClip read failed {relative_blob}@{bundle_offset}: {exc}", file=sys.stderr)
        if progress and (index == 1 or index % 25 == 0 or index == len(blobs)):
            emit_progress("scan_tw", index, len(blobs), unit="files", enabled=progress)
            print(f"TW [{index}/{len(blobs)}] targets={len(targets)} bundles={bundles}", flush=True)
    emit_progress("scan_tw", scanned, len(blobs), unit="files", complete=True, enabled=progress)
    if priority and max_clips > 0:
        targets = priority_targets[:max_clips]
        if len(targets) < max_clips:
            targets.extend(fallback_targets[: max_clips - len(targets)])
    stats = {
        "blob_count": len(blobs),
        "bundle_count": bundles,
        "audio_object_count": audio_objects,
        "selected_target_count": len(targets),
        "priority_pattern": priority.pattern if priority else None,
        "priority_target_count": len(priority_targets),
        "engine_versions": sorted(engines),
    }
    return targets, stats


def targets_from_index(
    index: dict[str, Any],
    root: Path,
    include: list[re.Pattern[str]],
    max_clips: int,
    priority: re.Pattern[str] | None,
    blob_filter: set[str] | None = None,
) -> tuple[list[TWClip], dict[str, Any]]:
    """Select targets from the reusable read-only voice index."""
    priority_targets: list[TWClip] = []
    fallback_targets: list[TWClip] = []
    selected_all: list[TWClip] = []
    for file_record in index["files"]:
        relative_blob = str(file_record["path"]).replace("\\", "/")
        if blob_filter is not None and relative_blob.casefold() not in blob_filter:
            continue
        blob_path = root / file_record["path"]
        for item in file_record["clips"]:
            name = str(item["name"])
            container = str(item["container"])
            if include and not any(pattern.search(name) or pattern.search(container) for pattern in include):
                continue
            target = TWClip(
                name=name,
                container=container,
                signature=dict(item["signature"]),
                blob_path=blob_path,
                bundle_offset=int(item["bundle_offset"]),
                path_id=int(item["path_id"]),
                resource_size=int(item.get("resource_size", 0) or 0),
            )
            selected_all.append(target)
            if priority and priority.search(name):
                priority_targets.append(target)
            else:
                fallback_targets.append(target)
    if priority and max_clips > 0:
        selected = priority_targets[:max_clips]
        if len(selected) < max_clips:
            selected.extend(fallback_targets[: max_clips - len(selected)])
    elif max_clips > 0:
        selected = selected_all[:max_clips]
    else:
        selected = selected_all
    stats = {
        "blob_count": len(index["files"]),
        "bundle_count": sum(int(item["bundles"]) for item in index["files"]),
        "audio_object_count": index["clip_count"],
        "selected_target_count": len(selected),
        "priority_pattern": priority.pattern if priority else None,
        "priority_target_count": len(priority_targets),
        "engine_versions": list(index.get("engine_versions", [])),
        "index_used": True,
    }
    return selected, stats


def select_jp_clip(
    name: str,
    container: str,
    by_full: dict[tuple[str, str], JPClip],
    by_name: dict[str, list[JPClip]],
) -> tuple[JPClip | None, str]:
    exact = by_full.get((name.casefold(), container))
    if exact:
        return exact, "name+container"
    candidates = by_name.get(name.casefold(), [])
    if len(candidates) == 1:
        return candidates[0], "unique-name"
    return None, "missing-or-ambiguous"


def scan_tw(
    root: Path,
    UnityPy: Any,
    by_full: dict[tuple[str, str], JPClip],
    by_name: dict[str, list[JPClip]],
    include: list[re.Pattern[str]],
    max_clips: int,
    progress: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    blobs = list(iter_blob_files(root))
    bundles = 0
    audio_objects = 0
    engines: set[str] = set()
    seen_targets: set[tuple[str, int, int]] = set()
    for index, blob_path in enumerate(blobs, 1):
        blob_data = blob_path.read_bytes()
        relative_blob = str(blob_path.relative_to(root)).replace("\\", "/")
        for bundle_offset, bundle_data in iter_unityfs_slices(blob_data):
            try:
                env = load_bundle(UnityPy, bundle_data)
            except Exception as exc:
                print(f"WARN TW bundle parse failed {relative_blob}@{bundle_offset}: {exc}", file=sys.stderr)
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
                    audio_objects += 1
                    if include and not any(pattern.search(name) or pattern.search(container) for pattern in include):
                        continue
                    jp_clip, match_kind = select_jp_clip(name, container, by_full, by_name)
                    key = (relative_blob, bundle_offset, int(obj.path_id))
                    if key in seen_targets:
                        continue
                    seen_targets.add(key)
                    if jp_clip is None:
                        continue
                    if max_clips > 0 and len(matches) >= max_clips:
                        continue
                    matches.append(
                        {
                            "tw": TWClip(
                                name, container, clip_signature(clip), blob_path, bundle_offset,
                                int(obj.path_id), int(getattr(getattr(clip, "m_Resource", None), "m_Size", 0) or 0),
                            ),
                            "jp": jp_clip,
                            "match_kind": match_kind,
                            "tw_length": float(getattr(clip, "m_Length", 0.0) or 0.0),
                            "jp_length": jp_clip.length,
                        }
                    )
                except Exception as exc:
                    print(f"WARN TW AudioClip read failed {relative_blob}@{bundle_offset}: {exc}", file=sys.stderr)
        if progress and (index == 1 or index % 25 == 0 or index == len(blobs)):
            print(f"TW [{index}/{len(blobs)}] matches={len(matches)} bundles={bundles}", flush=True)
    stats = {
        "blob_count": len(blobs),
        "bundle_count": bundles,
        "audio_object_count": audio_objects,
        "selected_match_count": len(matches),
        "engine_versions": sorted(engines),
    }
    return matches, stats


def compatible(tw: TWClip, jp: JPClip) -> tuple[bool, str]:
    if not tw.signature.get("has_external_resource") or not jp.signature.get("has_external_resource"):
        return False, "one side does not use an external resource"
    # Frequency and channel differences are handled by a real FSB5 transcode.
    for key in ("bits_per_sample", "compression_format", "load_type", "stream"):
        if tw.signature.get(key) != jp.signature.get(key):
            return False, f"format field differs: {key} ({tw.signature.get(key)!r} != {jp.signature.get(key)!r})"
    if tw.signature.get("channels") != jp.signature.get("channels"):
        return True, f"channel metadata adjusted: {tw.signature.get('channels')!r} -> {jp.signature.get('channels')!r}"
    return True, "compatible"


def copy_client(source: Path, destination: Path) -> None:
    if destination.exists():
        if any(destination.iterdir()):
            raise ValueError(f"Output copy already exists and is not empty: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
    total = sum(len(files) for _, _, files in os.walk(source))
    copied = 0
    emit_progress("copy", 0, total, unit="files")
    def copy_file(source_file: str, target_file: str) -> str:
        nonlocal copied
        result = shutil.copy2(source_file, target_file)
        copied += 1
        if copied < total:
            emit_progress("copy", copied, total, unit="files")
        return result
    shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=copy_file)
    emit_progress("copy", copied, total, unit="files", complete=True)


def hardlink_client(source: Path, destination: Path) -> None:
    """Create a disposable test copy without duplicating large package files.

    Asset package files are hard-linked and are therefore copy-on-write from
    the replacer's perspective (it atomically replaces modified blobs). Files
    outside the package directories are copied normally so launcher and client
    metadata cannot write through to the clean source.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"Source client directory is missing: {source}")
    if destination.exists():
        if any(destination.iterdir()):
            raise ValueError(f"Output copy already exists and is not empty: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
    package_roots = {name.casefold() for name in PACKAGE_DIRS}
    package_level = 1 if (source / "appdata").is_dir() else 0
    total = sum(len(files) for _, _, files in os.walk(source))
    copied = 0
    emit_progress("copy", 0, total, unit="files")
    for current, dirs, files in os.walk(source):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        inside_packages = (
            len(relative.parts) > package_level
            and relative.parts[package_level].casefold() in package_roots
        )
        for filename in files:
            source_file = current_path / filename
            target_file = target_dir / filename
            linked = False
            if inside_packages:
                try:
                    os.link(source_file, target_file)
                    linked = True
                except OSError:
                    # Cross-volume or unsupported filesystems fall back to a
                    # normal copy; correctness is more important than speed.
                    pass
            if not linked:
                shutil.copy2(source_file, target_file)
            copied += 1
            if copied < total:
                emit_progress("copy", copied, total, unit="files", operation="hardlink" if linked else "copy")
    emit_progress("copy", copied, total, unit="files", complete=True)


def json_dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def json_dump_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def commit_staged_blob_batch(
    output_root: Path,
    staged_blobs: dict[Path, Path],
    original_hashes: dict[Path, str],
    *,
    allow_length_change: bool = False,
) -> list[dict[str, Any]]:
    """Commit a fully prepared batch, restoring every attempted file on error."""

    staged_blobs = {Path(source).resolve(): Path(staged).resolve() for source, staged in staged_blobs.items()}
    original_hashes = {Path(source).resolve(): value for source, value in original_hashes.items()}
    if set(staged_blobs) != set(original_hashes):
        raise ValueError("Staged blob and original hash sets differ")
    if not staged_blobs:
        return []

    output_root = output_root.resolve()
    backup_root = output_root / ".MabinogiMobileVoiceMod_backup"
    manifest_path = output_root / "backup-manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing transaction manifest: {manifest_path}")

    staged_hashes: dict[Path, str] = {}
    backup_by_source: dict[Path, Path] = {}
    backups: list[dict[str, Any]] = []
    for source in sorted(staged_blobs):
        source = source.resolve()
        staged = staged_blobs[source].resolve()
        relative = source.relative_to(output_root)
        if not source.is_file() or not staged.is_file() or source == staged:
            raise ValueError(f"Missing or invalid staged blob for {relative}")
        if not source.is_relative_to(output_root):
            raise ValueError(f"Source blob is outside output root: {source}")
        expected = original_hashes[source]
        if sha256_file(source) != expected:
            raise RuntimeError(f"Source blob changed during preparation: {relative}")
        if not allow_length_change and source.stat().st_size != staged.stat().st_size:
            raise RuntimeError(f"Staged blob changed its original length: {relative}")
        staged_hashes[source] = sha256_file(staged)
        backup = backup_root / relative
        if backup.exists():
            raise FileExistsError(f"Refusing to overwrite existing blob backup: {backup}")
        backup_by_source[source] = backup
        backups.append({
            "path": str(relative).replace("\\", "/"),
            "backup": str(backup.relative_to(output_root)).replace("\\", "/"),
            "sha256_before": expected,
            "sha256_after": staged_hashes[source],
            "bytes": source.stat().st_size,
            "bytes_after": staged.stat().st_size,
        })

    created_backups: list[Path] = []
    attempted: list[Path] = []
    prepared = False
    try:
        for source in sorted(backup_by_source):
            backup = backup_by_source[source]
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
            created_backups.append(backup)
            if sha256_file(backup) != original_hashes[source]:
                raise RuntimeError(f"Blob backup verification failed: {source.relative_to(output_root)}")

        json_dump_atomic(manifest_path, {
            "format": 1,
            "generated_utc": now_utc(),
            "transaction_status": "prepared",
            "files": backups,
        })
        make_rollback(output_root, backups)
        prepared = True

        for source in sorted(backup_by_source):
            relative = source.relative_to(output_root)
            if sha256_file(source) != original_hashes[source]:
                raise RuntimeError(f"Source blob changed before commit: {relative}")
            staged = staged_blobs[source]
            if sha256_file(staged) != staged_hashes[source]:
                raise RuntimeError(f"Staged blob changed before commit: {relative}")
            fd, adjacent_temp = tempfile.mkstemp(
                prefix=source.name + ".", suffix=".commit", dir=source.parent
            )
            try:
                with os.fdopen(fd, "wb") as handle, staged.open("rb") as staged_handle:
                    shutil.copyfileobj(staged_handle, handle, length=1024 * 1024)
                    handle.flush()
                    os.fsync(handle.fileno())
                shutil.copystat(source, adjacent_temp)
                attempted.append(source)
                os.replace(adjacent_temp, source)
            finally:
                Path(adjacent_temp).unlink(missing_ok=True)
            if source.stat().st_size != staged.stat().st_size or sha256_file(source) != staged_hashes[source]:
                raise RuntimeError(f"Committed blob verification failed: {relative}")

        json_dump_atomic(manifest_path, {
            "format": 1,
            "generated_utc": now_utc(),
            "transaction_status": "committed",
            "files": backups,
        })
        return backups
    except BaseException as commit_error:
        rollback_errors: list[str] = []
        for source in reversed(attempted):
            backup = backup_by_source[source]
            fd, adjacent_temp = tempfile.mkstemp(
                prefix=source.name + ".", suffix=".rollback", dir=source.parent
            )
            try:
                with os.fdopen(fd, "wb") as handle, backup.open("rb") as saved:
                    shutil.copyfileobj(saved, handle, length=1024 * 1024)
                    handle.flush()
                    os.fsync(handle.fileno())
                shutil.copystat(backup, adjacent_temp)
                os.replace(adjacent_temp, source)
                if sha256_file(source) != original_hashes[source]:
                    raise RuntimeError("restored file hash does not match its backup")
            except BaseException as rollback_error:
                rollback_errors.append(f"{source}: {rollback_error}")
            finally:
                Path(adjacent_temp).unlink(missing_ok=True)

        if not prepared:
            for backup in created_backups:
                backup.unlink(missing_ok=True)
        manifest_value = {
            "format": 1,
            "generated_utc": now_utc(),
            "transaction_status": "rollback_incomplete" if rollback_errors else "rolled_back",
            "error": str(commit_error),
            "rollback_errors": rollback_errors,
            "files": backups if prepared else [],
        }
        manifest_error: BaseException | None = None
        try:
            if prepared:
                json_dump_atomic(manifest_path, manifest_value)
            elif manifest_path.exists():
                manifest_path.unlink()
        except BaseException as exc:
            manifest_error = exc

        details = "; ".join(rollback_errors)
        if manifest_error:
            details = "; ".join(filter(None, [details, f"manifest update failed: {manifest_error}"]))
        if isinstance(commit_error, (KeyboardInterrupt, SystemExit)):
            if details:
                print(f"Batch rollback issue: {details}", file=sys.stderr)
            raise
        if details:
            raise RuntimeError(
                f"Blob batch commit failed ({commit_error}); rollback/report also failed: {details}"
            ) from commit_error
        raise RuntimeError(f"Blob batch commit failed and was rolled back: {commit_error}") from commit_error


def commit_failure_report_status(
    output_root: Path, manifest_existed_before_commit: bool = False
) -> str:
    """Report only rollback states confirmed by the transaction manifest."""

    if manifest_existed_before_commit:
        return "commit_failed_before_mutation"
    manifest_path = output_root / "backup-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "commit_failed_before_mutation"
    except (OSError, json.JSONDecodeError):
        return "commit_failed_rollback_unknown"
    status = manifest.get("transaction_status") if isinstance(manifest, dict) else None
    if status == "rolled_back":
        return "commit_failed_rolled_back"
    if status == "rollback_incomplete":
        return "commit_failed_rollback_incomplete"
    if status is None:
        return "commit_failed_before_mutation"
    return "commit_failed_rollback_unknown"


def make_rollback(output_root: Path, backups: list[dict[str, Any]]) -> None:
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$root = Split-Path -Parent $MyInvocation.MyCommand.Path",
        "$backup = Join-Path $root '.MabinogiMobileVoiceMod_backup'",
    ]
    for item in backups:
        rel = item["path"].replace("/", "\\")
        lines.append(f"Copy-Item -LiteralPath (Join-Path $backup '{rel}') -Destination (Join-Path $root '{rel}') -Force")
    lines += ["Write-Host 'Rollback complete. The generated backup remains in .MabinogiMobileVoiceMod_backup.'"]
    (output_root / "rollback.ps1").write_text("\n".join(lines) + "\n", encoding="utf-8")


def ps_quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def make_swap_helpers(output_root: Path, source_root: Path) -> None:
    """Create opt-in directory swap helpers beside a generated copy.

    The copy operation itself remains non-destructive.  When source and
    output are siblings, the helper lets a user first rename the original to
    ``<name>-back`` and then give the tested copy the platform's default name.
    Existing backups are refused rather than overwritten.
    """
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if source_root.name.casefold().endswith("-back"):
        return
    if source_root.parent != output_root.parent or source_root == output_root:
        return
    backup_root = source_root.with_name(source_root.name + "-back")
    activate = [
        "$ErrorActionPreference = 'Stop'",
        f"$source = {ps_quote(source_root)}",
        f"$backup = {ps_quote(backup_root)}",
        "$generated = (Resolve-Path -LiteralPath $PSScriptRoot).Path",
        "if (-not (Test-Path -LiteralPath $source)) { throw \"Original client was not found: $source\" }",
        "if (Test-Path -LiteralPath $backup) { throw \"Backup already exists; refusing to overwrite: $backup\" }",
        "if ($generated -eq (Resolve-Path -LiteralPath $source).Path) { throw 'Run this helper from the generated copy.' }",
        "Move-Item -LiteralPath $source -Destination $backup",
        "try { Move-Item -LiteralPath $generated -Destination $source } catch { Move-Item -LiteralPath $backup -Destination $source; throw }",
        "Write-Host \"Activated generated copy at $source. Original is preserved at $backup.\"",
    ]
    restore = [
        "$ErrorActionPreference = 'Stop'",
        f"$source = {ps_quote(source_root)}",
        f"$backup = {ps_quote(backup_root)}",
        "$generated = $source + '-mod-staging'",
        "if (-not (Test-Path -LiteralPath $source)) { throw \"Active client was not found: $source\" }",
        "if (-not (Test-Path -LiteralPath $backup)) { throw \"Backup was not found: $backup\" }",
        "if (Test-Path -LiteralPath $generated) { throw \"Staging path already exists: $generated\" }",
        "Move-Item -LiteralPath $source -Destination $generated",
        "try { Move-Item -LiteralPath $backup -Destination $source } catch { Move-Item -LiteralPath $generated -Destination $source; throw }",
        "Write-Host \"Restored original client at $source. Generated copy is at $generated.\"",
    ]
    (output_root / "activate_generated_copy.ps1").write_text("\n".join(activate) + "\n", encoding="utf-8")
    (output_root / "restore_original_copy.ps1").write_text("\n".join(restore) + "\n", encoding="utf-8")


def write_replacement_summary(output_data_root: Path, reports: list[dict[str, Any]]) -> None:
    """Write a human-readable list so a small test batch can be located in-game."""
    replaced = [item for item in reports if item.get("status") == "replaced"]
    identical = [item for item in reports if item.get("status") == "already_identical"]
    metadata_adjusted = [item for item in reports if item.get("status") == "metadata_adjusted"]
    unchanged_source = [item for item in reports if item.get("source_payload_identical") is True]
    lines = [
        "Mabinogi Mobile JP voice replacement summary",
        f"Replaced clips: {len(replaced)}",
        f"Already identical and fully verified (no rewrite): {len(identical)}",
        f"Metadata adjusted without changing audio payload: {len(metadata_adjusted)}",
        f"Source audio identical to original TW payload: {len(unchanged_source)}",
        "Source equality alone does not identify the spoken language.",
        "",
        "The first test batch is selected by the tool's scan order. Names below are",
        "the Unity AudioClip names; they identify the cutscene/quest and VO sequence.",
        "",
    ]
    for index, item in enumerate(replaced, 1):
        lines.append(
            f"{index:04d}. {item.get('name', '')} | "
            f"bundle={item.get('blob', '')}@{item.get('bundle_offset', '')} | "
            f"old={item.get('old_resource_size', '')} bytes -> new={item.get('new_resource_size', '')} bytes"
        )
    if identical:
        lines.extend(["", "Already identical resources; no client bytes were changed:"])
        for item in identical:
            lines.append(
                f"- {item.get('name', '')} | "
                f"bundle={item.get('blob', '')}@{item.get('bundle_offset', '')} | "
                f"resource={item.get('new_resource_size', '')} bytes"
            )
    if unchanged_source:
        lines.extend(["", "Source audio identical to original TW payload; no audio-language change is established:"])
        for item in unchanged_source:
            lines.append(f"- {item.get('name', '')} | status={item.get('status', '')} | source={item.get('jp_source_blob', '')}")
    skipped_quality = [item for item in reports if item.get("status") == "skipped_quality"]
    if skipped_quality:
        lines.extend([
            "",
            "Skipped quality/capacity checks; the original TW resource was retained:",
        ])
        for item in skipped_quality:
            lines.append(
                f"- {item.get('name', '')} | "
                f"bundle={item.get('blob', '')}@{item.get('bundle_offset', '')} | "
                f"capacity={item.get('resource_capacity', '')} bytes | "
                f"reason={item.get('reason', 'no acceptable encoding') }"
            )
    (output_data_root / "replacement-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def preserve_bundle_size(bundle: bytes, target_size: int) -> bytes | None:
    """Pad a rebuilt UnityFS bundle without moving end-of-file block info."""
    if len(bundle) > target_size:
        return None
    if len(bundle) == target_size:
        return bundle
    first_null = bundle.find(b"\0", 12)
    if first_null < 0:
        return None
    second_null = bundle.find(b"\0", first_null + 1)
    if second_null < 0:
        return None

    size_offset = second_null + 1
    header_end = size_offset + 20
    if header_end > len(bundle):
        return None
    if int.from_bytes(bundle[size_offset : size_offset + 8], "big") != len(bundle):
        return None
    compressed_info_size = int.from_bytes(bundle[size_offset + 8 : size_offset + 12], "big")
    flags = int.from_bytes(bundle[size_offset + 16 : size_offset + 20], "big")
    if compressed_info_size > len(bundle) - header_end:
        return None

    padded = bytearray(bundle)
    padding_size = target_size - len(bundle)
    if flags & 0x80:  # ArchiveFlags.BlocksInfoAtTheEnd
        info_start = len(bundle) - compressed_info_size
        if info_start < header_end:
            return None
        padded[info_start:info_start] = b"\0" * padding_size
    else:
        padded.extend(b"\0" * padding_size)
    padded[size_offset : size_offset + 8] = target_size.to_bytes(8, "big")
    return bytes(padded)


def save_bundle_preserving_unchanged_files(
    bundle_file: Any,
    original_file_bytes: dict[str, bytes],
    changed_serialized_names: set[str],
    packer: str,
) -> bytes:
    """Save a bundle while passing untouched SerializedFiles through byte-for-byte."""
    from UnityPy.files.SerializedFile import SerializedFile  # type: ignore
    from UnityPy.streams import EndianBinaryReader  # type: ignore

    replacements: dict[str, Any] = {}
    try:
        for name, file_obj in list(bundle_file.files.items()):
            if name in changed_serialized_names or name not in original_file_bytes:
                continue
            if isinstance(file_obj, SerializedFile):
                reader = EndianBinaryReader(original_file_bytes[name])
                reader.flags = int(getattr(file_obj, "flags", 0))
                reader.name = name
                replacements[name] = file_obj
                bundle_file.files[name] = reader
        return bundle_file.save(packer)
    finally:
        for name, file_obj in replacements.items():
            bundle_file.files[name] = file_obj


def verify_changes_within_ranges(
    original: bytes,
    updated: bytes,
    allowed_ranges: list[tuple[int, int]],
    label: str,
) -> None:
    """Require equal-sized data and reject any change outside approved byte ranges."""

    if len(original) != len(updated):
        raise ValueError(f"{label} changed length ({len(original)} -> {len(updated)})")
    cursor = 0
    for start, end in sorted(allowed_ranges):
        if start < cursor or end < start or end > len(original):
            raise ValueError(f"Invalid or overlapping approved byte range in {label}: {start}:{end}")
        if original[cursor:start] != updated[cursor:start]:
            raise ValueError(f"Unexpected bytes changed outside target slots in {label}")
        cursor = end
    if original[cursor:] != updated[cursor:]:
        raise ValueError(f"Unexpected bytes changed outside target slots in {label}")


def replace_matches_preserve_structure(
    output_root: Path,
    matches: list[dict[str, Any]],
    UnityPy: Any,
    staging_root: Path,
    fsbank_dir: Path | None = None,
    fsbank_cache_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[Path, Path], dict[Path, str], list[str]]:
    """Replace resources while keeping bundle lengths and blob offsets fixed.

    Payloads are written only within the exact byte range described by the
    target AudioClip's original m_Resource.m_Size. Oversized payloads are
    transcoded to that capacity. Shared or overlapping ranges cannot be
    replaced safely in strict in-place mode and are reported as failures.
    """
    from UnityPy.streams import EndianBinaryWriter  # type: ignore

    # Process the smallest slots first. If a full strict batch contains an
    # impossible complete-duration replacement, this discovers the blocker
    # before spending time transcoding thousands of larger clips.
    matches = sorted(
        matches,
        key=lambda item: (
            int(getattr(item["tw"], "resource_size", 0) or 0),
            str(item["tw"].blob_path).casefold(),
            int(item["tw"].bundle_offset),
            int(item["tw"].path_id),
        ),
    )
    grouped: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        grouped[match["tw"].blob_path].append(match)
    print(f"Preparing {len(grouped)} blob batches.", flush=True)
    emit_progress("replace", 0, len(grouped), unit="blobs", complete=not grouped)
    reports: list[dict[str, Any]] = []
    staged_blobs: dict[Path, Path] = {}
    original_hashes: dict[Path, str] = {}
    warnings: list[str] = []
    processed_matches: set[int] = set()
    for blob_index, (source_blob, blob_matches) in enumerate(grouped.items(), 1):
        relative = source_blob.relative_to(output_root)
        print(f"Preparing blob {blob_index}/{len(grouped)}: {relative}", flush=True)
        emit_progress("replace", blob_index - 1, len(grouped), unit="blobs", operation="rebuild")
        original_blob = source_blob.read_bytes()
        original_blob_hash = sha256_bytes(original_blob)
        slices = list(iter_unityfs_slices(original_blob))
        replacements: dict[int, bytes] = {}
        for bundle_offset, bundle_data in slices:
            selected = [m for m in blob_matches if m["tw"].bundle_offset == bundle_offset]
            if not selected:
                continue
            processed_matches.update(id(match) for match in selected)
            env = load_bundle(UnityPy, bundle_data)
            original_file_contents = {
                name: bytes(file_obj.bytes)
                for name, file_obj in env.file.files.items()
                if hasattr(file_obj, "bytes")
            }
            # ``SerializedFile`` entries expose their bytes through
            # ``reader.bytes`` rather than a direct ``bytes`` attribute.
            # Keep the complete directory-name set separately; comparing it
            # with ``original_file_contents`` would incorrectly report every
            # normal bundle containing a SerializedFile as changed.
            original_file_names = set(env.file.files)
            before_hashes = {
                (obj.assets_file.name, int(obj.path_id)): (obj.type.name, sha256_bytes(obj.get_raw_data()))
                for obj in env.objects
            }
            objects = {int(obj.path_id): obj for obj in env.objects if obj.type.name == "AudioClip"}
            resource_slots: dict[str, list[tuple[int, int]]] = defaultdict(list)
            resource_slot_references: dict[tuple[str, int], int] = defaultdict(int)
            slot_sizes: dict[tuple[str, int], set[int]] = defaultdict(set)
            for obj in objects.values():
                try:
                    clip = obj.read()
                    resource = getattr(clip, "m_Resource", None)
                    name = resource_basename(getattr(resource, "m_Source", ""))
                    if name and name in env.file.files:
                        offset = int(getattr(resource, "m_Offset", 0))
                        size = int(getattr(resource, "m_Size", 0))
                        resource_slots[name].append((offset, size))
                        resource_slot_references[(name, offset)] += 1
                        slot_sizes[(name, offset)].add(size)
                except Exception:
                    continue
            capacities: dict[tuple[str, int], int] = {
                slot: next(iter(sizes)) if len(sizes) == 1 else 0
                for slot, sizes in slot_sizes.items()
            }
            overlapping_slots: set[tuple[str, int]] = set()
            for resource_name, slots in resource_slots.items():
                ordered = sorted(set(slots))
                for index, (offset, size) in enumerate(ordered):
                    if size <= 0:
                        continue
                    end = offset + size
                    for other_offset, other_size in ordered[index + 1 :]:
                        if other_offset >= end:
                            break
                        if other_size > 0:
                            overlapping_slots.add((resource_name, offset))
                            overlapping_slots.add((resource_name, other_offset))
            resource_buffers: dict[str, bytearray] = {}
            resource_flags: dict[str, tuple[int, str]] = {}
            resource_storage_modes: dict[tuple[str, int], str] = {}
            resource_storage_offsets: dict[tuple[str, int], int] = {}
            pending: list[tuple[dict[str, Any], Any, Any, JPClip, int, int, bytes, float | None, int, int, float | None, str | None]] = []
            used_slots: set[tuple[str, int]] = set()
            for match in selected:
                tw = match["tw"]
                jp = match["jp"]
                target = objects.get(tw.path_id)
                if target is None:
                    reports.append({"name": tw.name, "container": tw.container, "status": "failed", "reason": "object disappeared during rebuild"})
                    continue
                clip = target.read()
                resource = getattr(clip, "m_Resource", None)
                resource_name = resource_basename(getattr(resource, "m_Source", ""))
                if resource_name not in env.file.files:
                    reports.append({"name": tw.name, "container": tw.container, "status": "failed", "reason": "TW resource file missing"})
                    continue
                old_offset = int(getattr(resource, "m_Offset", 0) or 0)
                old_size = int(getattr(resource, "m_Size", 0) or 0)
                slot = (resource_name, old_offset)
                capacity = capacities.get(slot, old_size)
                resource_length = len(env.file.files[resource_name].bytes)
                if old_offset < 0 or old_size <= 0 or old_offset + old_size > resource_length:
                    reports.append({
                        "name": tw.name,
                        "container": tw.container,
                        "status": "failed",
                        "reason": "original AudioClip resource slot is outside its resource file",
                    })
                    continue
                original_payload = bytes(env.file.files[resource_name].bytes)[old_offset : old_offset + old_size]
                match["_original_resource_sha256"] = sha256_bytes(original_payload)
                match["_source_payload_identical"] = original_payload == jp.resource_bytes
                try:
                    identical_validation = validate_already_identical(clip, jp, original_payload, old_size)
                except Exception as exc:
                    reports.append({
                        "name": tw.name,
                        "container": tw.container,
                        "status": "failed",
                        "reason": f"identical resource failed complete decode validation: {exc}",
                    })
                    continue
                if identical_validation is not None:
                    reports.append({
                        "name": tw.name,
                        "container": tw.container,
                        "status": "already_identical",
                        "reason": "JP/TW resource bytes and AudioClip metadata are identical; complete decode verified; no rewrite required",
                        "match_kind": match["match_kind"],
                        "blob": str(source_blob.relative_to(output_root)).replace("\\", "/"),
                        "bundle_offset": bundle_offset,
                        "path_id": tw.path_id,
                        "old_resource_size": old_size,
                        "new_resource_size": old_size,
                        "resource_capacity": old_size,
                        "resource_storage_mode": "unchanged",
                        "resource_storage_offset": old_offset,
                        "new_resource_sha256": sha256_bytes(original_payload),
                        "original_resource_sha256": match["_original_resource_sha256"],
                        "source_payload_identical": True,
                        "jp_source_blob": jp.source_blob,
                        "jp_source_bundle_offset": jp.source_offset,
                        "output_channels": jp.signature.get("channels"),
                        "output_frequency": jp.signature.get("frequency"),
                        "output_length": jp.length,
                        "decoded_duration": identical_validation["duration"],
                        "structure_preserved": True,
                        "write_performed": False,
                    })
                    continue
                ok, reason = compatible(tw, jp)
                if not ok:
                    reports.append({"name": tw.name, "container": tw.container, "status": "failed", "reason": reason})
                    continue
                if slot in overlapping_slots:
                    reports.append({
                        "name": tw.name,
                        "container": tw.container,
                        "status": "failed",
                        "reason": "original AudioClip resource slot overlaps another resource slot",
                    })
                    continue
                if resource_slot_references[slot] > 1:
                    reports.append({
                        "name": tw.name,
                        "container": tw.container,
                        "status": "failed",
                        "reason": "original AudioClip resource slot is shared; strict in-place replacement cannot preserve both references",
                    })
                    continue
                if slot in used_slots:
                    reports.append({"name": tw.name, "container": tw.container, "status": "failed", "reason": "original AudioClip resource slot is selected more than once"})
                    continue
                payload = jp.resource_bytes
                processing_details: dict[str, Any] = {}
                output_compression_format = jp.signature.get("compression_format")
                compression_quality: float | None = None
                compression_encoder: str | None = None
                output_channels = jp.signature.get("channels")
                output_frequency = jp.signature.get("frequency")
                output_length: float | None = jp.length
                try:
                    fsb_info = parse_fsb5(payload)
                except Exception as exc:
                    reports.append({
                        "name": tw.name,
                        "container": tw.container,
                        "status": "failed",
                        "reason": f"Japanese source is not a supported FSB5 resource: {exc}",
                    })
                    continue
                if fsb_info["channels"] != output_channels or fsb_info["frequency"] != output_frequency:
                    reports.append({
                        "name": tw.name,
                        "container": tw.container,
                        "status": "failed",
                        "reason": "Japanese FSB5 header does not match its AudioClip channel/rate metadata",
                    })
                    continue
                if len(payload) > capacity:
                    try:
                        compressed = compress_fsb5_to_capacity(
                            payload,
                            capacity,
                            preferred_channels=tw.signature.get("channels"),
                            preferred_frequency=tw.signature.get("frequency"),
                            fsbank_dir=fsbank_dir,
                            fsbank_cache_dir=fsbank_cache_dir,
                        )
                    except Exception as exc:
                        reports.append({
                            "name": tw.name,
                            "container": tw.container,
                            "status": "failed",
                            "reason": f"oversized FSB5 could not be transcoded to the original {capacity}-byte slot: {exc}",
                        })
                        continue
                    if compressed is None:
                        reports.append({
                            "name": tw.name,
                            "container": tw.container,
                            "status": "skipped_quality",
                            "reason": (
                                f"no acceptable complete-duration encoding at or above "
                                f"{MIN_ACCEPTABLE_INPUT_FREQUENCY} Hz fit the original "
                                f"{capacity}-byte slot; original TW resource retained"
                            ),
                            "match_kind": match["match_kind"],
                            "blob": str(source_blob.relative_to(output_root)).replace("\\", "/"),
                            "bundle_offset": bundle_offset,
                            "path_id": tw.path_id,
                            "old_resource_size": old_size,
                            "new_resource_size": old_size,
                            "resource_capacity": capacity,
                            "resource_storage_mode": "unchanged",
                            "resource_storage_offset": old_offset,
                            "jp_source_blob": jp.source_blob,
                            "jp_source_bundle_offset": jp.source_offset,
                            "source_length": jp.length,
                            "structure_preserved": True,
                            "write_performed": False,
                        })
                        continue
                    payload, compression_quality, output_channels, output_frequency, output_length, compression_encoder = compressed
                    processing_details = compressed.details
                    output_compression_format = processing_details["compression_format"]
                    match["_encoding_details"] = processing_details
                    if len(payload) > capacity:
                        raise ValueError(f"Compressor returned an oversized payload for {tw.name}")
                    compressed_info = parse_fsb5(payload)
                    if compressed_info["channels"] != output_channels or compressed_info["frequency"] != output_frequency:
                        raise ValueError(f"Compressed FSB5 metadata mismatch for {tw.name}")
                    duration_tolerance = 1.0 / output_frequency
                    retained_duration = processing_details.get("trim", {}).get("retained_duration", jp.length)
                    if abs(output_length - retained_duration) > duration_tolerance + 1e-6:
                        raise ValueError(
                            f"Transcode changed retained speech duration for {tw.name}: "
                            f"{retained_duration:.6f}s -> {output_length:.6f}s"
                        )
                if resource_name not in resource_buffers:
                    resource_buffers[resource_name] = bytearray(env.file.files[resource_name].bytes)
                    resource_flags[resource_name] = (
                        int(getattr(env.file.files[resource_name], "flags", 0)),
                        str(getattr(env.file.files[resource_name], "endian", ">")),
                    )
                buffer = resource_buffers[resource_name]
                if len(payload) > capacity:
                    raise ValueError(f"Payload exceeds original resource slot after compression: {tw.name}")
                end = old_offset + len(payload)
                buffer[old_offset:end] = payload
                if len(payload) < old_size:
                    buffer[end : old_offset + old_size] = b"\0" * (old_size - len(payload))
                new_offset = old_offset
                storage_mode = "in-place"
                resource.m_Offset = new_offset
                resource.m_Size = len(payload)
                clip.m_Length = output_length
                if output_compression_format is not None:
                    clip.m_CompressionFormat = output_compression_format
                if output_channels is not None and tw.signature.get("channels") != output_channels:
                    clip.m_Channels = output_channels
                if output_frequency is not None and tw.signature.get("frequency") != output_frequency:
                    clip.m_Frequency = output_frequency
                clip.save()
                used_slots.add(slot)
                object_key = (target.assets_file.name, int(target.path_id))
                resource_storage_modes[object_key] = storage_mode
                resource_storage_offsets[object_key] = new_offset
                pending.append((match, target, clip, jp, old_size, capacity, payload, compression_quality, output_channels, output_frequency, output_length, compression_encoder))
            if not pending:
                continue
            original_file_bytes = {
                name: bytes(file_obj.reader.bytes)
                for name, file_obj in env.file.files.items()
                if hasattr(file_obj, "reader") and hasattr(file_obj.reader, "bytes")
            }
            changed_serialized_names = {
                target.assets_file.name
                for _, target, _, _, _, _, _, _, _, _, _, _ in pending
            }
            # AudioClip typetree edits normally keep the exact serialized
            # object size. In that common case patch the original SerializedFile
            # data bytes directly, avoiding UnityPy's full-file reserialization
            # and its metadata/layout growth. Fall back to normal serialization
            # for the rare object whose encoded size changes.
            from UnityPy.streams import EndianBinaryReader  # type: ignore
            serialized_patches: dict[str, bytearray] = {
                name: bytearray(original_file_bytes[name])
                for name in changed_serialized_names
                if name in original_file_bytes
            }
            direct_patch_ok = True
            for _, target, _, _, _, _, _, _, _, _, _, _ in pending:
                name = target.assets_file.name
                raw = getattr(target, "data", None)
                original = original_file_bytes.get(name)
                if raw is None or original is None or len(raw) != int(target.byte_size):
                    direct_patch_ok = False
                    break
                start = int(target.byte_start)
                end = start + len(raw)
                if start < 0 or end > len(serialized_patches[name]):
                    direct_patch_ok = False
                    break
                serialized_patches[name][start:end] = raw
            if not direct_patch_ok:
                raise ValueError(
                    f"Cannot preserve non-target SerializedFile bytes for {relative}@{bundle_offset}"
                )
            for name, patched in serialized_patches.items():
                reader = EndianBinaryReader(bytes(patched))
                reader.flags = int(getattr(env.file.files[name], "flags", 0))
                reader.name = name
                env.file.files[name] = reader
            for resource_name, buffer in resource_buffers.items():
                writer = EndianBinaryWriter(bytes(buffer), endian=resource_flags[resource_name][1])
                writer.flags = resource_flags[resource_name][0]
                writer.name = resource_name
                env.file.files[resource_name] = writer
            env.file.is_changed = True
            rebuilt = save_bundle_preserving_unchanged_files(
                env.file, original_file_bytes, changed_serialized_names, "original"
            )
            packer_used = "original"
            original_rebuilt_size = len(rebuilt)
            hc_rebuilt_size: int | None = None
            lzma_rebuilt_size: int | None = None
            preserved = preserve_bundle_size(rebuilt, len(bundle_data))
            if preserved is None:
                # UnityFS supports high-compression LZ4.  Rebuilding with
                # lz4hc often recovers the small compression overhead caused
                # by replacing audio packets while keeping the bundle's
                # offset and total length unchanged.
                rebuilt_hc = save_bundle_preserving_unchanged_files(
                    env.file, original_file_bytes, changed_serialized_names, "lz4hc"
                )
                hc_rebuilt_size = len(rebuilt_hc)
                preserved = preserve_bundle_size(rebuilt_hc, len(bundle_data))
                if preserved is not None:
                    rebuilt = rebuilt_hc
                    packer_used = "lz4hc"
            if preserved is None:
                # UnityFS readers support the native LZMA block mode. It can
                # recover space when the replacement audio has a less
                # compressible byte pattern than the original LZ4 blocks,
                # without lowering audio quality or moving any bundle.
                rebuilt_lzma = save_bundle_preserving_unchanged_files(
                    env.file, original_file_bytes, changed_serialized_names, "lzma"
                )
                lzma_rebuilt_size = len(rebuilt_lzma)
                preserved = preserve_bundle_size(rebuilt_lzma, len(bundle_data))
                if preserved is not None:
                    rebuilt = rebuilt_lzma
                    packer_used = "lzma"
            if preserved is None:
                for match, target, clip, jp, old_size, capacity, payload, compression_quality, output_channels, output_frequency, output_length, compression_encoder in pending:
                    reports.append({
                        "name": match["tw"].name,
                        "container": match["tw"].container,
                        "status": "failed",
                        "reason": (
                            f"preserve-structure bundle grew (original={original_rebuilt_size}, "
                            f"lz4hc={hc_rebuilt_size}, lzma={lzma_rebuilt_size}, target={len(bundle_data)})"
                        ),
                    })
                continue
            check = load_bundle(UnityPy, preserved)
            after_objects = {(obj.assets_file.name, int(obj.path_id)): obj for obj in check.objects}
            if after_objects.keys() != before_hashes.keys():
                raise ValueError(f"Preserved bundle lost/added objects: {relative}@{bundle_offset}")
            pending_keys = {(target.assets_file.name, int(target.path_id)) for _, target, _, _, _, _, _, _, _, _, _, _ in pending}
            for key, (type_name, original_hash) in before_hashes.items():
                obj = after_objects[key]
                if obj.type.name != type_name:
                    raise ValueError(f"Preserved object type differs: {key}")
                if key not in pending_keys and sha256_bytes(obj.get_raw_data()) != original_hash:
                    raise ValueError(f"Non-target object changed in preserved bundle: {key}")
            if set(check.file.files) != original_file_names:
                raise ValueError(f"Preserved UnityFS directory entries changed: {relative}@{bundle_offset}")
            for name, before_bytes in original_file_contents.items():
                after_bytes = bytes(check.file.files[name].bytes)
                if name in resource_buffers:
                    allowed = [
                        (offset, offset + capacities[(name, offset)])
                        for resource_name, offset in used_slots
                        if resource_name == name
                    ]
                    verify_changes_within_ranges(
                        before_bytes,
                        after_bytes,
                        allowed,
                        f"{relative}@{bundle_offset}:{name}",
                    )
                elif name in serialized_patches:
                    if after_bytes != bytes(serialized_patches[name]):
                        raise ValueError(f"Unexpected SerializedFile bytes changed: {name}")
                elif after_bytes != before_bytes:
                    raise ValueError(f"Non-target UnityFS file changed: {name}")
            for match, target, clip, jp, old_size, capacity, payload, compression_quality, output_channels, output_frequency, output_length, compression_encoder in pending:
                obj = after_objects[(target.assets_file.name, int(target.path_id))]
                checked = obj.read()
                resource = checked.m_Resource
                resource_name = resource_basename(resource.m_Source)
                resource_data = bytes(check.file.files[resource_name].bytes)
                actual = resource_data[resource.m_Offset : resource.m_Offset + resource.m_Size]
                if actual != payload:
                    raise ValueError(f"Preserved audio resource mismatch: {jp.name}")
                try:
                    validate_fsb5_payload(
                        payload,
                        expected_duration=match.get("_encoding_details", {}).get("trim", {}).get("retained_duration", jp.length),
                        expected_channels=output_channels,
                        expected_frequency=output_frequency,
                        max_bytes=capacity,
                    )
                except Exception as exc:
                    raise ValueError(f"Preserved FSB5 failed full-duration validation: {jp.name}: {exc}") from exc
                if not math.isclose(float(checked.m_Length), output_length, rel_tol=0.0, abs_tol=1e-5):
                    raise ValueError(f"AudioClip duration metadata mismatch: {jp.name}")
                output_compression_format = match.get("_encoding_details", {}).get("compression_format", jp.signature.get("compression_format"))
                if checked.m_CompressionFormat != output_compression_format:
                    raise ValueError(f"AudioClip compression format mismatch: {jp.name}")
                if checked.m_Channels != output_channels or checked.m_Frequency != output_frequency:
                    raise ValueError(f"AudioClip format metadata mismatch: {jp.name}")
                reports.append({
                    "name": match["tw"].name,
                    "container": match["tw"].container,
                    "status": "prepared",
                    "match_kind": match["match_kind"],
                    "blob": str(source_blob.relative_to(output_root)).replace("\\", "/"),
                    "bundle_offset": bundle_offset,
                    "path_id": match["tw"].path_id,
                    "old_resource_size": old_size,
                    "new_resource_size": len(payload),
                    "resource_capacity": capacity,
                    "resource_storage_mode": resource_storage_modes[(target.assets_file.name, int(target.path_id))],
                    "resource_storage_offset": resource_storage_offsets[(target.assets_file.name, int(target.path_id))],
                    "bundle_size": len(bundle_data),
                    "new_resource_sha256": sha256_bytes(payload),
                    "original_resource_sha256": match["_original_resource_sha256"],
                    "source_payload_identical": match["_source_payload_identical"],
                    "jp_source_blob": jp.source_blob,
                    "jp_source_bundle_offset": jp.source_offset,
                    "compression_quality": compression_quality,
                    "compression_encoder": compression_encoder,
                    "output_channels": output_channels,
                    "output_frequency": output_frequency,
                    "output_length": output_length,
                    "source_length": jp.length,
                    "output_compression_format": output_compression_format,
                    "trim": match.get("_encoding_details", {}).get("trim", {}),
                    "encoding_details": match.get("_encoding_details", {}),
                    "format_adjustment": "; ".join(
                        item
                        for item in (
                            f"channel metadata adjusted: {match['tw'].signature.get('channels')!r} -> {output_channels!r}"
                            if match["tw"].signature.get("channels") != output_channels else None,
                            f"frequency adjusted: {match['tw'].signature.get('frequency')!r} -> {output_frequency!r}"
                            if match["tw"].signature.get("frequency") != output_frequency else None,
                            f"duration after exact-zero edge trimming: {jp.length:.6f}s -> {output_length:.6f}s"
                            if output_length is not None and abs(jp.length - output_length) > 0.001 else None,
                        )
                        if item
                    ) or None,
                    "structure_preserved": True,
                    "bundle_packer": packer_used,
                    "bundle_rebuilt_sizes": {
                        "original": original_rebuilt_size,
                        "lz4hc": hc_rebuilt_size,
                        "lzma": lzma_rebuilt_size,
                    },
                })
            replacements[bundle_offset] = preserved
        if sha256_file(source_blob) != original_blob_hash:
            raise RuntimeError(f"Source blob changed during preparation: {relative}")
        if replacements:
            output = bytearray()
            cursor = 0
            for offset, original_bundle in slices:
                output.extend(original_blob[cursor:offset])
                output.extend(replacements.get(offset, original_bundle))
                cursor = offset + len(original_bundle)
            output.extend(original_blob[cursor:])
            verify_changes_within_ranges(
                original_blob,
                bytes(output),
                [(offset, offset + len(bundle)) for offset, bundle in slices if offset in replacements],
                str(relative),
            )
            staged_blob = staging_root / relative
            staged_blob.parent.mkdir(parents=True, exist_ok=True)
            staged_blob.write_bytes(output)
            staged_blobs[source_blob.resolve()] = staged_blob.resolve()
            original_hashes[source_blob.resolve()] = original_blob_hash
            print(f"Prepared structure-preserving blob: {relative} ({len(replacements)} bundles)", flush=True)
        emit_progress("replace", blob_index, len(grouped), unit="blobs", complete=blob_index == len(grouped), operation="verified")
    for match in matches:
        if id(match) not in processed_matches:
            tw = match["tw"]
            reports.append({
                "name": tw.name,
                "container": tw.container,
                "status": "failed",
                "blob": str(tw.blob_path.relative_to(output_root)).replace("\\", "/"),
                "bundle_offset": tw.bundle_offset,
                "path_id": tw.path_id,
                "reason": "target bundle was not found in the source blob during preparation",
            })
    return reports, staged_blobs, original_hashes, warnings


def replace_matches(
    output_root: Path,
    matches: list[dict[str, Any]],
    UnityPy: Any,
    preserve_structure: bool = False,
    staging_root: Path | None = None,
    fsbank_dir: Path | None = None,
    fsbank_cache_dir: Path | None = None,
) -> Any:
    if preserve_structure:
        if staging_root is None:
            raise ValueError("Strict structure-preserving mode requires a staging directory")
        return replace_matches_preserve_structure(
            output_root,
            matches,
            UnityPy,
            staging_root,
            fsbank_dir=fsbank_dir,
            fsbank_cache_dir=fsbank_cache_dir,
        )
    from UnityPy.streams import EndianBinaryWriter  # type: ignore

    grouped: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        grouped[match["tw"].blob_path].append(match)
    reports: list[dict[str, Any]] = []
    backups: list[dict[str, Any]] = []
    warnings: list[str] = []
    backup_root = output_root / ".MabinogiMobileVoiceMod_backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    emit_progress("replace", 0, len(grouped), unit="blobs", complete=not grouped)
    for blob_index, (source_blob, blob_matches) in enumerate(grouped.items(), 1):
        emit_progress("replace", blob_index - 1, len(grouped), unit="blobs", operation="rebuild")
        relative = source_blob.relative_to(output_root)
        data = source_blob.read_bytes()
        slices = list(iter_unityfs_slices(data))
        replacements: dict[int, bytes] = {}
        for bundle_offset, bundle_data in slices:
            selected = [m for m in blob_matches if m["tw"].bundle_offset == bundle_offset]
            if not selected:
                continue
            env = load_bundle(UnityPy, bundle_data)
            before_hashes = {(obj.assets_file.name, int(obj.path_id)): (obj.type.name, sha256_bytes(obj.get_raw_data())) for obj in env.objects}
            objects = {int(obj.path_id): obj for obj in env.objects if obj.type.name == "AudioClip"}
            expected: dict[tuple[str, int], JPClip] = {}
            resource_buffers: dict[str, bytearray] = {}
            resource_flags: dict[str, tuple[int, str]] = {}
            changed = 0
            for match in selected:
                tw = match["tw"]
                jp = match["jp"]
                target = objects.get(tw.path_id)
                if target is None:
                    warnings.append(f"object disappeared during rebuild: {relative}@{bundle_offset}:{tw.path_id}")
                    continue
                clip = target.read()
                ok, reason = compatible(tw, jp)
                if not ok:
                    reports.append({"name": tw.name, "container": tw.container, "status": "skipped", "reason": reason})
                    continue
                resource = getattr(clip, "m_Resource", None)
                resource_name = resource_basename(getattr(resource, "m_Source", ""))
                if resource_name not in env.file.files:
                    reports.append({"name": tw.name, "container": tw.container, "status": "skipped", "reason": "TW resource file missing"})
                    continue
                original = bytes(env.file.files[resource_name].bytes)
                old_offset = int(getattr(resource, "m_Offset", 0) or 0)
                old_size = int(getattr(resource, "m_Size", 0) or 0)
                if old_offset < 0 or old_size <= 0 or old_offset + old_size > len(original):
                    raise ValueError(f"Original AudioClip resource range is invalid: {tw.name}")
                original_payload = original[old_offset:old_offset + old_size]
                source_identical = original_payload == jp.resource_bytes
                original_sha256 = sha256_bytes(original_payload)
                identical_validation = validate_already_identical(
                    clip, jp, original_payload, old_size, validator=validate_fsb5_payload_fmod,
                )
                if identical_validation is not None:
                    reports.append({
                        "name": tw.name, "container": tw.container, "status": "already_identical",
                        "reason": "source audio and AudioClip metadata are identical; complete FMOD decode verified; no rewrite required",
                        "match_kind": match["match_kind"], "blob": relative.as_posix(),
                        "bundle_offset": bundle_offset, "path_id": tw.path_id,
                        "old_resource_size": old_size, "new_resource_size": old_size,
                        "original_resource_sha256": original_sha256, "new_resource_sha256": original_sha256,
                        "source_payload_identical": True, "write_performed": False,
                        "jp_source_blob": jp.source_blob, "jp_source_bundle_offset": jp.source_offset,
                        "decoded_duration": identical_validation["duration"], "format_adjustment": None,
                    })
                    continue
                if source_identical:
                    validate_fsb5_payload_fmod(original_payload, jp.length, jp.signature.get("channels"), jp.signature.get("frequency"), old_size)
                else:
                    if resource_name not in resource_buffers:
                        resource_buffers[resource_name] = bytearray(original)
                        resource_flags[resource_name] = (int(getattr(env.file.files[resource_name], "flags", 0)), str(getattr(env.file.files[resource_name], "endian", ">")))
                    buffer = resource_buffers[resource_name]
                    new_offset = aligned(len(buffer))
                    if new_offset > len(buffer):
                        buffer.extend(b"\0" * (new_offset - len(buffer)))
                    buffer.extend(jp.resource_bytes)
                    resource.m_Offset = new_offset
                    resource.m_Size = len(jp.resource_bytes)
                clip.m_Length = jp.length
                if tw.signature.get("channels") != jp.signature.get("channels"):
                    clip.m_Channels = jp.signature.get("channels")
                if tw.signature.get("frequency") != jp.signature.get("frequency"):
                    clip.m_Frequency = jp.signature.get("frequency")
                if tw.signature.get("compression_format") != jp.signature.get("compression_format"):
                    clip.m_CompressionFormat = jp.signature.get("compression_format")
                clip.save()
                expected[(target.assets_file.name, int(target.path_id))] = jp
                changed += 1
                reports.append(
                    {
                        "name": tw.name,
                        "container": tw.container,
                        "status": "metadata_adjusted" if source_identical else "replaced",
                        "match_kind": match["match_kind"],
                        "blob": str(source_blob.relative_to(output_root)).replace("\\", "/"),
                        "bundle_offset": bundle_offset,
                        "path_id": tw.path_id,
                        "old_resource_size": old_size,
                        "new_resource_size": len(jp.resource_bytes),
                        "new_resource_sha256": sha256_bytes(jp.resource_bytes),
                        "original_resource_sha256": original_sha256,
                        "source_payload_identical": source_identical,
                        "write_performed": True,
                        "jp_source_blob": jp.source_blob,
                        "jp_source_bundle_offset": jp.source_offset,
                        "format_adjustment": reason if reason != "compatible" else None,
                    }
                )
            if changed:
                for resource_name, buffer in resource_buffers.items():
                    writer = EndianBinaryWriter(bytes(buffer), endian=resource_flags[resource_name][1])
                    writer.flags = resource_flags[resource_name][0]
                    writer.name = resource_name
                    env.file.files[resource_name] = writer
                env.file.is_changed = True
                rebuilt = env.file.save("original")
                emit_progress("replace", blob_index - 1, len(grouped), unit="blobs", operation="verify")
                check = load_bundle(UnityPy, rebuilt)
                after_objects = {(obj.assets_file.name, int(obj.path_id)): obj for obj in check.objects}
                if after_objects.keys() != before_hashes.keys():
                    raise ValueError(f"Rebuilt bundle lost/added objects: {relative}@{bundle_offset}")
                for key, (type_name, original_hash) in before_hashes.items():
                    obj = after_objects[key]
                    if obj.type.name != type_name:
                        raise ValueError(f"Rebuilt object type differs: {key}")
                    if key not in expected and sha256_bytes(obj.get_raw_data()) != original_hash:
                        raise ValueError(f"Non-target object changed: {key}")
                for key, jp in expected.items():
                    clip = after_objects[key].read()
                    res = clip.m_Resource
                    res_data = bytes(check.file.files[resource_basename(res.m_Source)].bytes)
                    actual = res_data[res.m_Offset : res.m_Offset + res.m_Size]
                    if actual != jp.resource_bytes or not math.isclose(clip.m_Length, jp.length, abs_tol=1e-5):
                        raise ValueError(f"Rebuilt audio resource mismatch: {jp.name}")
                    if (
                        clip.m_Channels != jp.signature.get("channels")
                        or clip.m_Frequency != jp.signature.get("frequency")
                        or clip.m_CompressionFormat != jp.signature.get("compression_format")
                    ):
                        raise ValueError(f"Rebuilt audio channel metadata mismatch: {jp.name}")
                    if not clip.samples:
                        raise ValueError(f"Rebuilt audio could not be decoded: {jp.name}")
                replacements[bundle_offset] = rebuilt
        if replacements:
            backup_file = backup_root / source_blob.relative_to(output_root)
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_blob, backup_file)
            backups.append(
                {
                    "path": str(source_blob.relative_to(output_root)).replace("\\", "/"),
                    "backup": str(backup_file.relative_to(output_root)).replace("\\", "/"),
                    "sha256_before": sha256_file(backup_file),
                }
            )
            output = bytearray()
            cursor = 0
            for offset, original_bundle in slices:
                output.extend(data[cursor:offset])
                output.extend(replacements.get(offset, original_bundle))
                cursor = offset + len(original_bundle)
            output.extend(data[cursor:])
            output = bytearray(rewrite_blob_end_table(bytes(output)))
            backups[-1]["sha256_after"] = sha256_bytes(bytes(output))
            json_dump(output_root / "backup-manifest.json", {"format": 1, "generated_utc": now_utc(), "files": backups})
            make_rollback(output_root, backups)
            fd, temp_path = tempfile.mkstemp(prefix=source_blob.name + ".", suffix=".tmp", dir=source_blob.parent)
            emit_progress("replace", blob_index - 1, len(grouped), unit="blobs", operation="commit")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(output)
                os.replace(temp_path, source_blob)
            finally:
                Path(temp_path).unlink(missing_ok=True)
            print(f"Patched and verified: {relative} ({len(blob_matches)} selected objects)", flush=True)
        emit_progress("replace", blob_index, len(grouped), unit="blobs", complete=blob_index == len(grouped), operation="verified")
    return reports, backups, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jp-root", type=Path, help="Your Japanese client root (or its appdata directory)")
    parser.add_argument("--cache-dir", type=Path, help="Directory containing bundled resource_cache.json/bin")
    parser.add_argument(
        "--fsbank-dir",
        type=Path,
        help="Directory containing native FSBank DLLs (fsbank.dll, fmod.dll, libfsbvorbis64.dll)",
    )
    parser.add_argument(
        "--fsbank-cache",
        type=Path,
        help="Writable cache directory used by native FSBank (defaults to .fsbank-cache)",
    )
    parser.add_argument("--tw-root", type=Path, help="Your TW game root (or its appdata directory)")
    parser.add_argument("--output-copy", type=Path, help="New game-root directory for a patched TW copy")
    parser.add_argument("--dry-run", action="store_true", help="Only scan; never copy or write")
    parser.add_argument("--max-clips", type=int, default=5, help="Maximum replacements (0 = all, default: 5)")
    parser.add_argument("--include", action="append", default=[], help="Regex filter for clip name/container; repeatable")
    parser.add_argument(
        "--blob",
        action="append",
        default=[],
        help="Only replace clips from this relative .blob path; repeatable (for package-level bisection)",
    )
    parser.add_argument("--priority", help="Regex whose matching clips are preferred for a small batch")
    parser.add_argument("--force", action="store_true", help="Allow writing when client engine/version checks are inconclusive")
    parser.add_argument("--hardlink-copy", action="store_true", help="Create a fast disposable test copy using hard-linked package files")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preserve-structure", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--full-rebuild", action="store_true", help="完整重建模式（默认）：保留日服原始 FSB5，并更新 .blob 累计结束偏移表")
    parser.add_argument("--no-progress", action="store_true", help="Suppress periodic scan progress")
    parser.add_argument("--report-dir", type=Path, help="Directory for scan reports; defaults to output copy or current directory")
    return parser.parse_args()


def prompt_path(label: str) -> Path:
    value = input(f"{label}: ").strip().strip('"')
    return Path(value)


def main() -> int:
    args = parse_args()
    try:
        # ``getattr`` keeps programmatic callers and older test harnesses
        # compatible with the optional native encoder arguments.
        fsbank_arg = getattr(args, "fsbank_dir", None)
        fsbank_cache_arg = getattr(args, "fsbank_cache", None)
        fsbank_dir = fsbank_arg.expanduser().resolve() if fsbank_arg else None
        fsbank_cache_dir = fsbank_cache_arg.expanduser().resolve() if fsbank_cache_arg else None
        jp_game_root = jp_data_root = None
        if args.jp_root:
            jp_game_root, jp_data_root = client_data_root(args.jp_root, "JP root")
        tw_game_root, tw_data_root = client_data_root(
            args.tw_root or prompt_path("港澳台服游戏根目录（也可填写 appdata）"),
            "TW root",
        )
        if args.max_clips < 0:
            raise ValueError("--max-clips must be >= 0")
        if args.preserve_structure and getattr(args, "full_rebuild", False):
            raise ValueError("--preserve-structure and --full-rebuild are mutually exclusive")
        include = [re.compile(value, re.IGNORECASE) for value in args.include]
        blob_filter = {
            value.replace("\\", "/").strip("/").casefold()
            for value in args.blob
            if value.strip()
        } or None
        priority = re.compile(args.priority, re.IGNORECASE) if args.priority and args.max_clips > 0 else None
        output_game_root = None
        if args.output_copy:
            output_game_root = args.output_copy.expanduser().resolve()
            if output_game_root.name.casefold() == "appdata":
                output_game_root = output_game_root.parent
        if output_game_root and output_game_root == tw_game_root:
            raise ValueError("--output-copy must be different from --tw-root")
        if output_game_root and tw_game_root in output_game_root.parents:
            raise ValueError("--output-copy must not be inside the source TW directory")
        if output_game_root and not args.dry_run:
            assert_not_running()
        UnityPy = load_unitypy()
        print("Indexing TW voice targets...", flush=True)
        emit_progress("scan_tw", operation="index", enabled=not args.no_progress)
        # The first index build is parallel and read-only.  Subsequent test
        # runs select targets from JSON without reparsing every Unity bundle.
        try:
            from resource_voice_index import get_index, verify_files
            workers = max(1, min(8, os.cpu_count() or 2))
            tw_index = get_index(
                tw_data_root,
                package_root(),
                "tw",
                workers,
                not args.no_progress,
            )
            tw_targets, tw_stats = targets_from_index(
                tw_index, tw_data_root, include, args.max_clips, priority, blob_filter
            )
            verify_files(tw_data_root, tw_index, {str(t.blob_path.relative_to(tw_data_root)).replace("\\", "/") for t in tw_targets})
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            if args.max_clips > 0:
                print(f"WARN reusable index unavailable ({exc}); falling back to direct scan.", file=sys.stderr)
            tw_targets, tw_stats = scan_tw_targets(
                tw_data_root, UnityPy, include, args.max_clips, not args.no_progress, priority, blob_filter
            )
        print(f"Selected {len(tw_targets)} target clips (maximum requested: {'all' if args.max_clips == 0 else args.max_clips}).", flush=True)
        emit_progress("scan_tw", 1, 1, unit="index", complete=True, enabled=not args.no_progress)
        if not tw_targets and output_game_root and not args.dry_run and not args.preserve_structure:
            raise RuntimeError("No TW voice targets matched the requested filters; no output copy was created.")
        wanted_names = {target.name.casefold() for target in tw_targets}
        emit_progress("scan_jp", enabled=not args.no_progress)
        if jp_data_root:
            cache_parent = (args.cache_dir or package_root()).expanduser().resolve()
            jp_cache_key = sha256_bytes(json.dumps({
                "jp_manifest": manifest_info(jp_data_root),
                "wanted_names": sorted(wanted_names),
            }, ensure_ascii=True, sort_keys=True).encode("utf-8"))
            jp_cache_dir = cache_parent / ".jp-scan-cache" / jp_cache_key
            cached_jp = load_selected_jp_cache(jp_cache_dir, jp_cache_key)
            if cached_jp is not None:
                jp_by_full, jp_by_name, jp_stats = cached_jp
                print(
                    f"Using cached JP scan ({len(jp_by_full)} selected clips); source manifest and target set match.",
                    flush=True,
                )
                emit_progress("scan_jp", len(jp_by_full), len(jp_by_full), unit="clips", complete=True, operation="cache", enabled=not args.no_progress)
            else:
                print("Scanning matching JP resources (first run reads all JP .blob files)...", flush=True)
                jp_by_full, jp_by_name, jp_stats = scan_jp(jp_data_root, UnityPy, not args.no_progress, wanted_names)
                try:
                    save_selected_jp_cache(jp_cache_dir, jp_by_full, jp_stats.get("engine_versions", []), jp_cache_key)
                    jp_stats["selected_cache"] = True
                    jp_stats["cache_key"] = jp_cache_key
                    print(f"Saved selected JP scan cache: {jp_cache_dir}", flush=True)
                except OSError as exc:
                    print(f"WARN JP scan cache could not be saved: {exc}", file=sys.stderr)
        else:
            cache_dir = (args.cache_dir or package_root()).resolve()
            print(f"Loading bundled resource cache: {cache_dir}", flush=True)
            jp_by_full, jp_by_name, jp_stats = load_resource_cache(cache_dir)
            emit_progress("scan_jp", len(jp_by_full), len(jp_by_full), unit="clips", complete=True, operation="cache", enabled=not args.no_progress)
        matches: list[dict[str, Any]] = []
        unmatched_reports: list[dict[str, Any]] = []
        for target in tw_targets:
            jp_clip, match_kind = select_jp_clip(target.name, target.container, jp_by_full, jp_by_name)
            if jp_clip is None:
                unmatched_reports.append({
                    "name": target.name,
                    "container": target.container,
                    "status": "failed",
                    "blob": str(target.blob_path.relative_to(tw_data_root)).replace("\\", "/"),
                    "bundle_offset": target.bundle_offset,
                    "path_id": target.path_id,
                    "reason": "no matching Japanese voice resource was found",
                })
                continue
            matches.append({"tw": target, "jp": jp_clip, "match_kind": match_kind, "tw_length": None, "jp_length": jp_clip.length})
        if args.max_clips > 0 and len(matches) > args.max_clips:
            raise RuntimeError(
                f"Safety check failed: {len(matches)} compatible matches exceed the requested maximum {args.max_clips}."
            )
        print(
            f"Selected {len(matches)} compatible clips for replacement "
            f"(maximum requested: {'all' if args.max_clips == 0 else args.max_clips}).",
            flush=True,
        )
        engines = sorted(set(jp_stats["engine_versions"]) | set(tw_stats["engine_versions"]))
        version_ok = bool(engines) and all(version.startswith(SUPPORTED_ENGINE_PREFIX) for version in engines)
        if not version_ok and output_game_root and not args.dry_run and not args.force:
            raise RuntimeError(f"Unsupported or unknown Unity engine versions {engines}; use --force only after offline review")
        scan_report = {
            "format": 1,
            "generated_utc": now_utc(),
            "jp_root": str(jp_game_root) if jp_game_root else None,
            "tw_root": str(tw_game_root),
            "jp_data_root": str(jp_data_root) if jp_data_root else None,
            "tw_data_root": str(tw_data_root),
            "jp_manifest": manifest_info(jp_data_root) if jp_data_root else [],
            "tw_manifest": manifest_info(tw_data_root),
            "jp": jp_stats,
            "tw": tw_stats,
            "selected_matches": len(matches),
            "unmatched_targets": len(unmatched_reports),
            "max_clips": args.max_clips,
            "include": args.include,
            "blob_filter": sorted(blob_filter) if blob_filter else [],
            "priority": args.priority if args.max_clips > 0 else None,
            "copy_mode": "hardlink-package" if args.hardlink_copy else "full-copy",
            "replacement_mode": "in-place" if args.preserve_structure else "full-rebuild",
            "fsbank_dir": str(fsbank_dir) if fsbank_dir else None,
            "fsbank_cache": str(fsbank_cache_dir) if fsbank_cache_dir else None,
            "write_performed": False,
            "version_check": {"engine_versions": engines, "supported_prefix": SUPPORTED_ENGINE_PREFIX, "passed": version_ok},
        }
        if args.dry_run or not args.output_copy:
            report_dir = (args.report_dir or Path.cwd()).resolve()
            report_dir.mkdir(parents=True, exist_ok=True)
            json_dump(report_dir / "scan-report.json", scan_report)
            print(f"Found {len(matches)} compatible name candidates (before format checks). Report: {report_dir / 'scan-report.json'}")
            print("Dry-run only; no client files were copied or modified.")
            return 0
        output_root = output_game_root
        assert output_root is not None
        if args.hardlink_copy:
            hardlink_client(tw_game_root, output_root)
            print("Created fast hard-link test copy; only modified package files use separate storage.", flush=True)
        else:
            copy_client(tw_game_root, output_root)
        output_data_root = output_root / "appdata" if (output_root / "appdata").is_dir() else output_root
        scan_report["write_performed"] = True
        report_dir = (args.report_dir or output_data_root).resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        json_dump(report_dir / "scan-report.json", scan_report)
        print(f"Found {len(matches)} compatible name candidates (before format checks). Report: {report_dir / 'scan-report.json'}")
        # Repoint matches from the source TW path to the copied path.
        for match in matches:
            source_blob = match["tw"].blob_path
            relative = source_blob.relative_to(tw_data_root)
            match["tw"].blob_path = output_data_root / relative
        backups: list[dict[str, Any]] = []
        warnings: list[str] = []
        if args.preserve_structure:
            with tempfile.TemporaryDirectory(prefix=".voice-stage-", dir=output_data_root) as stage_name:
                stage_root = Path(stage_name)
                try:
                    reports, staged_blobs, original_hashes, warnings = replace_matches(
                        output_data_root,
                        matches,
                        UnityPy,
                        preserve_structure=True,
                        staging_root=stage_root,
                        fsbank_dir=fsbank_dir,
                        fsbank_cache_dir=fsbank_cache_dir,
                    )
                except Exception as exc:
                    reports = unmatched_reports + [{
                        "status": "failed",
                        "reason": f"batch preparation aborted: {exc}",
                    }]
                    json_dump(output_data_root / "patch-report.json", {
                        "format": 1,
                        "generated_utc": now_utc(),
                        "transaction_status": "preparation_failed",
                        "replacements": reports,
                        "warnings": warnings,
                    })
                    write_replacement_summary(output_data_root, reports)
                    raise
                reports.extend(unmatched_reports)
                prepared_count = sum(item.get("status") == "prepared" for item in reports)
                already_identical_count = sum(item.get("status") == "already_identical" for item in reports)
                skipped_quality_count = sum(item.get("status") == "skipped_quality" for item in reports)
                satisfied_count = prepared_count + already_identical_count
                # Prepared clips are not processed until the staged blobs are
                # committed. If preflight or commit fails, only verified
                # no-ops and explicit quality skips count as processed.
                processed_count = already_identical_count + skipped_quality_count
                blockers = [
                    item for item in reports
                    if item.get("status") not in {"prepared", "already_identical", "skipped_quality"}
                ]
                # A skipped quality result deliberately leaves the TW resource
                # untouched and is safe to commit alongside other prepared
                # replacements. Any structural, format, or matching failure
                # remains a hard blocker and prevents every staged blob from
                # being committed.
                if not tw_targets or blockers or len(matches) != len(tw_targets):
                    if not tw_targets:
                        no_targets = {
                            "status": "failed",
                            "reason": "no TW voice targets matched the requested filters; no replacement was prepared",
                        }
                        blockers.append(no_targets)
                        reports.append(no_targets)
                    elif not blockers:
                        blockers.append({
                            "status": "failed",
                            "reason": "not every selected TW target has exactly one prepared replacement or verified identical JP resource",
                        })
                    for item in reports:
                        if item.get("status") == "prepared":
                            item["status"] = "not_committed"
                    json_dump(output_data_root / "patch-report.json", {
                        "format": 1,
                        "generated_utc": now_utc(),
                        "transaction_status": "preflight_failed",
                        "prepared_count": prepared_count,
                        "committed_count": 0,
                        "already_identical_count": already_identical_count,
                        "satisfied_count": already_identical_count,
                        "skipped_count": skipped_quality_count,
                        "skipped_quality_count": skipped_quality_count,
                        "processed_count": processed_count,
                        "replacements": reports,
                        "warnings": warnings,
                    })
                    write_replacement_summary(output_data_root, reports)
                    reasons = sorted({str(item.get("reason", "unknown")) for item in blockers})
                    raise RuntimeError(
                        f"严格批次预检失败：{len(blockers)} 个问题，未提交任何 .blob。"
                        f"详见 {output_data_root / 'patch-report.json'}。原因：{'；'.join(reasons[:5])}"
                    )
                try:
                    manifest_existed_before_commit = False
                    emit_progress("finalize", 0, 4, unit="steps", operation="commit")
                    manifest_existed_before_commit = (output_data_root / "backup-manifest.json").exists()
                    backups = commit_staged_blob_batch(output_data_root, staged_blobs, original_hashes)
                except Exception as exc:
                    for item in reports:
                        if item.get("status") == "prepared":
                            item["status"] = "not_committed"
                    json_dump(output_data_root / "patch-report.json", {
                        "format": 1,
                        "generated_utc": now_utc(),
                        "transaction_status": commit_failure_report_status(
                            output_data_root,
                            manifest_existed_before_commit,
                        ),
                        "commit_error": str(exc),
                        "prepared_count": prepared_count,
                        "committed_count": 0,
                        "already_identical_count": already_identical_count,
                        "satisfied_count": already_identical_count,
                        "skipped_count": skipped_quality_count,
                        "skipped_quality_count": skipped_quality_count,
                        "processed_count": processed_count,
                        "replacements": reports,
                        "warnings": warnings,
                    })
                    write_replacement_summary(output_data_root, reports)
                    raise
            for item in reports:
                if item.get("status") == "prepared":
                    item["status"] = "replaced"
        else:
            # The production path is a complete UnityFS/.blob rebuild. It
            # keeps the original JP FSB5 payload and updates the outer blob
            # end table after bundle sizes change.
            reports, backups, warnings = replace_matches(output_data_root, matches, UnityPy)
            reports.extend(unmatched_reports)
        emit_progress("finalize", 1, 4, unit="steps", operation="reports")
        skipped_quality = [item for item in reports if item.get("status") == "skipped_quality"]
        failed = [
            item for item in reports
            if item.get("status") not in {"replaced", "already_identical", "metadata_adjusted", "skipped_quality"}
        ]
        replaced = [item for item in reports if item.get("status") == "replaced"]
        already_identical = [item for item in reports if item.get("status") == "already_identical"]
        metadata_adjusted = [item for item in reports if item.get("status") == "metadata_adjusted"]
        unchanged_source_count = sum(item.get("source_payload_identical") is True for item in reports)
        satisfied_count = len(replaced) + len(already_identical) + len(metadata_adjusted)
        processed_count = satisfied_count + len(skipped_quality)
        json_dump(output_data_root / "patch-report.json", {
            "format": 1,
            "generated_utc": now_utc(),
            "transaction_status": "committed_with_skips" if skipped_quality and not failed else "committed" if not failed else "failed",
            "replacement_mode": "in-place" if args.preserve_structure else "full-rebuild",
            "prepared_count": len(replaced) + len(metadata_adjusted),
            "committed_count": len(replaced) + len(metadata_adjusted),
            "audio_replaced_count": len(replaced),
            "already_identical_count": len(already_identical),
            "metadata_adjusted_count": len(metadata_adjusted),
            "unchanged_source_count": unchanged_source_count,
            "satisfied_count": satisfied_count,
            "skipped_count": len(skipped_quality),
            "skipped_quality_count": len(skipped_quality),
            "processed_count": processed_count,
            "replacements": reports,
            "warnings": warnings,
        })
        write_replacement_summary(output_data_root, reports)
        emit_progress("finalize", 2, 4, unit="steps", operation="backup_report")
        if not args.preserve_structure:
            json_dump(output_data_root / "backup-manifest.json", {"format": 1, "generated_utc": now_utc(), "files": backups})
            make_rollback(output_data_root, backups)
        json_dump(output_data_root / "client-version.json", {
            "format": 1,
            "generated_utc": now_utc(),
            "jp_root": str(jp_game_root) if jp_game_root else None,
            "cache_dir": str((args.cache_dir or package_root()).resolve()) if not jp_data_root else None,
            "tw_root": str(tw_game_root),
            "tw_data_root": str(tw_data_root),
            "output_root": str(output_root),
            "output_data_root": str(output_data_root),
            "jp_manifest": manifest_info(jp_data_root) if jp_data_root else [],
            "tw_manifest": manifest_info(tw_data_root),
            "engine_versions": engines,
            "fsbank_dir": str(fsbank_dir) if fsbank_dir else None,
            "fsbank_cache": str(fsbank_cache_dir) if fsbank_cache_dir else None,
        })
        make_swap_helpers(output_root, tw_game_root)
        emit_progress("finalize", 3, 4, unit="steps", operation="final_checks")
        print(f"Created patched copy: {output_root}")
        print(
            f"Replaced {len(replaced)} clips; already identical and verified "
            f"{len(already_identical)}; skipped for quality/capacity "
            f"{len(skipped_quality)}; metadata adjusted {len(metadata_adjusted)}; "
            f"unchanged source audio {unchanged_source_count}; failed {len(failed)}."
        )
        if skipped_quality:
            print(
                "Kept the original TW resource for skipped clips; see "
                f"{output_data_root / 'replacement-summary.txt'}.",
                flush=True,
            )
        if failed:
            reasons = sorted({str(item.get("reason", "unknown")) for item in failed})
            raise RuntimeError(
                f"批次未完成：{len(failed)} 个目标因真实错误未替换。详见 {output_data_root / 'patch-report.json'}。"
                f" 原因：{'；'.join(reasons[:5])}"
            )
        print("The original JP/TW directories were not modified. Review patch-report.json and test offline first.")
        emit_progress("finalize", 4, 4, unit="steps", complete=True)
        emit_progress("done", 1, 1, complete=True)
        return 0
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
