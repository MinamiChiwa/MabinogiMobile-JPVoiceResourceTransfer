#!/usr/bin/env python3
"""Extract Japanese voice AudioClips from the Mabinogi Mobile JP blobs.

The game stores many UnityFS bundles concatenated inside custom .blob files.
UnityPy can read each UnityFS slice directly, so the extractor leaves both
client installations untouched and writes ordinary WAV files plus a manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


VOICE_TOKEN = re.compile(r"(?:^|[_/])vo(?:[_/]|$)", re.IGNORECASE)
UNITY_SIGNATURE = b"UnityFS"
AUDIO_SIGNATURES = (b"FSB5", b"ID3", b"OggS", b"RIFF", b"fLaC", b"BKHD")


def iter_blob_files(root: Path) -> Iterator[Path]:
    """Yield package blobs in stable order."""

    for directory_name in (
        "AssetPackages",
        "ResourcesPackage",
        "PlatformResourcePackages",
    ):
        directory = root / directory_name
        if directory.is_dir():
            yield from sorted(directory.rglob("*.blob"))


def iter_unityfs_slices(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (offset, complete UnityFS bundle) pairs from one .blob file."""

    cursor = 0
    while True:
        offset = data.find(UNITY_SIGNATURE, cursor)
        if offset < 0:
            return

        # UnityFS header: signature, format version, player version, engine
        # version, then a big-endian 64-bit bundle size.
        header_cursor = offset + 8
        if header_cursor + 4 > len(data):
            return
        first_null = data.find(b"\0", header_cursor + 4)
        if first_null < 0:
            return
        second_null = data.find(b"\0", first_null + 1)
        if second_null < 0 or second_null + 9 > len(data):
            return

        size = struct.unpack(">Q", data[second_null + 1 : second_null + 9])[0]
        end = offset + size
        if size <= 0 or end > len(data):
            cursor = offset + 1
            continue

        yield offset, data[offset:end]
        cursor = end


def is_voice_clip(name: str, container: str) -> bool:
    """Match voiced lines while excluding ordinary BGM/SFX clips."""

    name_lower = name.lower()
    container_lower = container.lower()
    return "/voice/" in container_lower or bool(VOICE_TOKEN.search(name_lower))


def safe_file_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip(" .")
    stem = stem[:120] or "unnamed"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL"}:
        stem = "_" + stem
    return stem


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jp-root",
        type=Path,
        default=Path(r"D:\Game\MabinogiMJapan\MabinogiMobile_JP"),
        help="Japanese client root",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("jp_voice_pack"),
        help="Output directory containing audio/ and voice_manifest.json",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow writing into an existing output directory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.jp_root.resolve()
    output = args.output.resolve()

    if not root.is_dir():
        print(f"JP root does not exist: {root}", file=sys.stderr)
        return 2
    if output.exists() and any(output.iterdir()) and not args.overwrite:
        print(
            f"Output is not empty: {output}\n"
            "Use --overwrite when you intend to regenerate it.",
            file=sys.stderr,
        )
        return 2

    audio_dir = output / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Import lazily so --help works without UnityPy installed.
    try:
        import UnityPy  # type: ignore
    except ImportError:
        print("UnityPy is required. Install it with: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    blobs = list(iter_blob_files(root))
    if not blobs:
        print(f"No .blob files found below {root}", file=sys.stderr)
        return 2

    records: dict[str, dict[str, object]] = {}
    used_stems: dict[str, str] = {}
    duplicate_differences = 0
    parsed_bundles = 0
    parsed_audio_clips = 0

    for blob_index, blob_path in enumerate(blobs, start=1):
        data = blob_path.read_bytes()
        relative_blob = str(blob_path.relative_to(root)).replace("\\", "/")

        for bundle_index, (bundle_offset, bundle_data) in enumerate(iter_unityfs_slices(data)):
            if not any(signature in bundle_data for signature in AUDIO_SIGNATURES):
                continue

            parsed_bundles += 1
            try:
                environment = UnityPy.load(bundle_data)
            except Exception as exc:  # pragma: no cover - depends on third-party parser
                print(f"WARN parse failed {relative_blob}@{bundle_offset}: {exc}", file=sys.stderr)
                continue

            for obj in environment.objects:
                if obj.type.name != "AudioClip":
                    continue
                try:
                    clip = obj.read()
                    name = str(getattr(clip, "m_Name", "") or "")
                    container = str(getattr(obj, "container", "") or "")
                except Exception:
                    continue

                parsed_audio_clips += 1
                if not name or not is_voice_clip(name, container):
                    continue

                try:
                    samples = clip.samples
                except Exception as exc:  # pragma: no cover - parser dependent
                    print(f"WARN samples failed {name}: {exc}", file=sys.stderr)
                    continue
                if not samples:
                    continue

                sample_name, sample_data = next(iter(samples.items()))
                audio_bytes = bytes(sample_data)
                stem = safe_file_stem(name)
                previous_name = used_stems.get(stem)
                if previous_name is not None and previous_name != name:
                    stem = f"{stem}_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"
                used_stems[stem] = name

                sha256 = hashlib.sha256(audio_bytes).hexdigest()
                previous = records.get(name)
                if previous is not None:
                    if previous["sha256"] == sha256:
                        continue
                    duplicate_differences += 1
                    # A duplicate path can occur in multiple bundles. Keep the
                    # largest copy, which is least likely to be a truncated one.
                    if int(previous["bytes"]) >= len(audio_bytes):
                        continue

                relative_audio = f"audio/{stem}.wav"
                (output / relative_audio).write_bytes(audio_bytes)
                records[name] = {
                    "file": relative_audio,
                    "container": container,
                    "sample_name": str(sample_name),
                    "bytes": len(audio_bytes),
                    "sha256": sha256,
                    "source_blob": relative_blob,
                    "source_bundle_offset": bundle_offset,
                    "source_bundle_index": bundle_index,
                }

        if blob_index == 1 or blob_index % 50 == 0 or blob_index == len(blobs):
            print(
                f"[{blob_index}/{len(blobs)}] {relative_blob} "
                f"voice={len(records)} bundles={parsed_bundles}",
                flush=True,
            )

    manifest = {
        "format": 1,
        "source": "MabinogiMobile_JP",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "voice_count": len(records),
        "parsed_bundles": parsed_bundles,
        "parsed_audio_clips": parsed_audio_clips,
        "duplicate_differences": duplicate_differences,
        "clips": dict(sorted(records.items(), key=lambda item: item[0].lower())),
    }
    (output / "voice_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(records)} clips to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
