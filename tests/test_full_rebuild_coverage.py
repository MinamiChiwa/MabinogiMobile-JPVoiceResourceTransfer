import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import transfer_jp_voice_resources as transfer


class FullRebuildCoverageTests(unittest.TestCase):
    def run_fixture(self, *, original=b"sourceFSB", source=b"sourceFSB", original_length=1.0):
        temp = tempfile.TemporaryDirectory(prefix="voice-full-coverage-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        blob = root / "AssetPackages" / "0010.blob"
        blob.parent.mkdir()
        blob.write_bytes(b"original-bundle")
        clip = SimpleNamespace(
            m_Channels=1, m_Frequency=44100, m_BitsPerSample=16,
            m_CompressionFormat=2, m_LoadType=1, m_Stream=None,
            m_Length=original_length, samples={"decoded.wav": b"decoded"},
            m_Resource=SimpleNamespace(m_Source="audio.resS", m_Offset=0, m_Size=len(original)),
        )
        clip.save = lambda: None
        obj = SimpleNamespace(
            type=SimpleNamespace(name="AudioClip"), path_id=1,
            assets_file=SimpleNamespace(name="audio.assets"), read=lambda: clip,
            get_raw_data=lambda: struct.pack("<fQQ", clip.m_Length, clip.m_Resource.m_Offset, clip.m_Resource.m_Size),
        )
        env = SimpleNamespace(
            objects=[obj], file=SimpleNamespace(
                files={"audio.resS": SimpleNamespace(bytes=original, flags=0, endian=">")},
                save=lambda *_: b"rebuilt-bundle",
            ),
        )
        jp = transfer.JPClip(
            "Vo_NPC_Flora_OpenShop_01", "assets/sound/voice/npc/flora/shop.wav",
            1.0, transfer.clip_signature(clip), source, "jp.resS",
            "AssetPackages/0638.blob", 10, 1,
        )
        tw = transfer.TWClip(jp.name, jp.container, jp.signature, blob, 0, 1, len(original))
        with (
            mock.patch.object(transfer, "iter_unityfs_slices", return_value=[(0, b"original-bundle")]),
            mock.patch.object(transfer, "load_bundle", return_value=env),
            mock.patch.object(transfer, "validate_fsb5_payload_fmod", return_value={"duration": 1.0}, create=True),
            mock.patch.object(transfer, "rewrite_blob_end_table", side_effect=lambda data: data),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            reports, backups, warnings = transfer.replace_matches(root, [{"tw": tw, "jp": jp, "match_kind": "name+container"}], object())
        return blob, clip, env, reports, backups, warnings

    def test_equal_audio_and_metadata_do_not_rewrite_the_blob(self):
        blob, clip, _, reports, backups, warnings = self.run_fixture()
        self.assertEqual(reports[0]["status"], "already_identical")
        self.assertEqual(blob.read_bytes(), b"original-bundle")
        self.assertEqual(backups, [])
        self.assertEqual(warnings, [])
        self.assertEqual(clip.m_Resource.m_Offset, 0)
        self.assertTrue(reports[0]["source_payload_identical"])
        self.assertFalse(reports[0]["write_performed"])

    def test_equal_audio_needing_metadata_update_keeps_the_original_payload_slot(self):
        blob, clip, env, reports, backups, _ = self.run_fixture(original_length=0.5)
        self.assertEqual(reports[0]["status"], "metadata_adjusted")
        self.assertEqual(blob.read_bytes(), b"rebuilt-bundle")
        self.assertEqual(clip.m_Length, 1.0)
        self.assertEqual(clip.m_Resource.m_Offset, 0)
        self.assertEqual(bytes(env.file.files["audio.resS"].bytes), b"sourceFSB")
        self.assertTrue(reports[0]["source_payload_identical"])
        self.assertEqual(reports[0]["original_resource_sha256"], reports[0]["new_resource_sha256"])
        self.assertEqual(len(backups), 1)

    def test_changed_source_audio_has_original_hash_and_counts_as_replaced(self):
        _, _, _, reports, _, _ = self.run_fixture(source=b"differentFSB")
        self.assertEqual(reports[0]["status"], "replaced")
        self.assertIn("source_payload_identical", reports[0])
        self.assertFalse(reports[0]["source_payload_identical"])
        self.assertNotEqual(reports[0]["original_resource_sha256"], reports[0]["new_resource_sha256"])

    def test_fmod_validation_rejects_an_incomplete_decoded_clip(self):
        validate = getattr(transfer, "validate_fsb5_payload_fmod", None)
        self.assertIsNotNone(validate, "Full rebuild needs complete FMOD decode validation for no-ops")
        wav = io.BytesIO()
        with wave.open(wav, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(44100)
            writer.writeframes(b"\0\0" * 3)
        import fmod_toolkit
        with (
            mock.patch.object(transfer, "parse_fsb5", return_value={"channels": 1, "frequency": 44100, "sample_count": 4}),
            mock.patch.object(fmod_toolkit, "raw_to_wav", return_value={"clip.wav": wav.getvalue()}),
        ):
            with self.assertRaisesRegex(ValueError, "frames"):
                validate(b"FSB", expected_duration=4 / 44100, expected_channels=1, expected_frequency=44100)


if __name__ == "__main__":
    unittest.main()
