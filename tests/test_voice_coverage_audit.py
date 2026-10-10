import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_voice_coverage as audit


def unityfs(payload):
    prefix = b"UnityFS\0" + b"\0\0\0\6" + b"2021.3\0" + b"51f1\0"
    return prefix + struct.pack(">Q", len(prefix) + 8 + len(payload)) + payload


class VoiceCoverageAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="voice-coverage-audit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.package = self.root / "package"
        self.package.mkdir()
        self.original = self.root / "original"
        self.installed = self.root / "installed"
        self.original.mkdir()
        self.installed.mkdir()
        self.name = "Vo_NPC_Flora_OpenShop_01"
        self.container = "assets/sound/voice/npc/dunbarton/soundgroup_npc_flora/shop.wav"
        self.payload = b"voice-payload"
        self.metadata = {"format": 1, "source": "MabinogiMobile_JP", "clip_count": 1, "payload_bytes": len(self.payload), "clips": [{
            "name": self.name, "container": self.container, "offset": 0,
            "size": len(self.payload), "sha256": "b00c2f36f70d9a88ebd90df139f77c100947947e7353c547d6491a93f89d7b5c",
            "source_blob": "AssetPackages/0638.blob",
        }]}
        # This fixture's known payload hash is calculated only to create input
        # metadata; audit expectations below use independent boolean/count facts.
        self.metadata["clips"][0]["sha256"] = audit.sha256(self.payload)
        (self.package / "resource_cache.bin").write_bytes(self.payload)
        self.write_metadata()

    def write_metadata(self):
        (self.package / "resource_cache.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def test_corrupt_cache_is_rejected_instead_of_recording_its_new_hash(self):
        (self.package / "resource_cache.bin").write_bytes(b"broken-source")
        with self.assertRaisesRegex(ValueError, "checksum"):
            audit.cache_records(self.package)

    def test_resource_range_outside_cache_is_rejected(self):
        self.metadata["clips"][0]["offset"] = -1
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "range"):
            audit.cache_records(self.package)

    def test_ambiguous_source_name_does_not_match_an_unrelated_container(self):
        select = getattr(audit, "select_cache_record", None)
        self.assertIsNotNone(select, "Audit must distinguish missing source from ambiguous names")
        one = dict(self.metadata["clips"][0], container="voice/one.wav")
        two = dict(self.metadata["clips"][0], container="voice/two.wav")
        selected, kind = select(self.name, "voice/other.wav", {}, {self.name.casefold(): [one, two]})
        self.assertIsNone(selected)
        self.assertEqual(kind, "ambiguous-name")

    def test_unique_source_name_uses_the_existing_transfer_fallback(self):
        select = getattr(audit, "select_cache_record", None)
        self.assertIsNotNone(select)
        one = dict(self.metadata["clips"][0], container="voice/one.wav")
        selected, kind = select(self.name, "voice/other.wav", {}, {self.name.casefold(): [one]})
        self.assertEqual(selected, one)
        self.assertEqual(kind, "unique-name")

    def test_installed_bundle_offset_is_resolved_by_ordinal_after_previous_bundle_grows(self):
        old_first, old_second = unityfs(b"first"), unityfs(b"second")
        new_first, new_second = unityfs(b"first-much-longer"), unityfs(b"second-new")
        original_data, installed_data = old_first + old_second, new_first + new_second
        (self.original / "voice.blob").write_bytes(original_data)
        (self.installed / "voice.blob").write_bytes(installed_data)
        index_path = self.root / "index.json"
        index_path.write_text(json.dumps({"format": 1, "files": [{
            "path": "voice.blob", "sha256": audit.sha256(original_data), "clips": [{
                "name": self.name, "container": self.container, "bundle_offset": len(old_first), "path_id": 1,
            }],
        }]}), encoding="utf-8")
        def payloads(data, offset):
            self.assertEqual(offset, len(old_first) if data == original_data else len(new_first))
            return {1: {"name": self.name, "container": self.container, "sha256": audit.sha256(self.payload), "bytes": len(self.payload)}}
        with mock.patch.object(audit, "bundle_payloads", side_effect=payloads):
            report = audit.audit(package=self.package, index_path=index_path, original=self.original, installed=self.installed)
        self.assertEqual(report["summary"]["objects"], 1)
        self.assertEqual(report["summary"]["installed_matches_cache"], 1)
        self.assertEqual(report["summary"]["source_payload_identical"], 1)

    def test_escaped_index_path_cannot_read_outside_the_client(self):
        index_path = self.root / "index.json"
        index_path.write_text(json.dumps({"format": 1, "files": [{
            "path": "../secret.blob", "clips": [{"name": self.name, "container": self.container, "bundle_offset": 0, "path_id": 1}],
        }]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "path"):
            audit.audit(package=self.package, index_path=index_path, original=self.original, installed=self.installed)


if __name__ == "__main__":
    unittest.main()
