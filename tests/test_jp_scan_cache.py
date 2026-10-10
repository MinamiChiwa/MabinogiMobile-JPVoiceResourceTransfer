import importlib.util
import tempfile
import sys
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(PACKAGE))
spec = importlib.util.spec_from_file_location("jp_cache_transfer", PACKAGE / "transfer_jp_voice_resources.py")
transfer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = transfer
spec.loader.exec_module(transfer)


class JpScanCacheTests(unittest.TestCase):
    def test_selected_jp_scan_cache_round_trips_payload_and_key(self):
        clip = transfer.JPClip(
            "Vo_NPC_Aranwen_Test", "voice/test", 1.0,
            {"channels": 1, "frequency": 44100, "bits_per_sample": 16,
             "compression_format": 1, "load_type": 2, "stream": None,
             "has_external_resource": True},
            b"original-fsb5", "x.resource", "AssetPackages/0001.blob", 123, 9,
        )
        with tempfile.TemporaryDirectory(prefix="jp-scan-cache-") as temporary:
            root = Path(temporary) / "cache"
            payload, metadata = transfer.resource_cache_value({(clip.name.casefold(), clip.container): clip}, ["2021.3.51f1"], "key")
            root.mkdir()
            (root / "resource_cache.bin").write_bytes(payload)
            (root / "resource_cache.json").write_text(__import__("json").dumps(metadata), encoding="utf-8")
            hit = transfer.load_selected_jp_cache(root, "key")
            miss = transfer.load_selected_jp_cache(root, "other-key")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit[0][(clip.name.casefold(), clip.container)].resource_bytes, b"original-fsb5")
        self.assertIsNone(miss)


if __name__ == "__main__":
    unittest.main()
