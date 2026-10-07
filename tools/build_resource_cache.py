#!/usr/bin/env python3
"""Build the bundled compressed-resource cache from a local JP client."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from transfer_jp_voice_resources import load_unitypy, scan_jp, sha256_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jp-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.jp_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    UnityPy = load_unitypy()
    by_full, _by_name, stats = scan_jp(root, UnityPy, progress=True, wanted_names=None)
    payload = bytearray()
    records = []
    for record in sorted(by_full.values(), key=lambda item: (item.name.casefold(), item.container)):
        offset = len(payload)
        payload.extend(record.resource_bytes)
        records.append(
            {
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
            }
        )
    (output / "resource_cache.bin").write_bytes(bytes(payload))
    metadata = {
        "format": 1,
        "source": "MabinogiMobile_JP",
        "generated_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "engine_versions": stats.get("engine_versions", []),
        "clip_count": len(records),
        "payload_bytes": len(payload),
        "clips": records,
    }
    (output / "resource_cache.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} compressed clips ({len(payload)} bytes) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
