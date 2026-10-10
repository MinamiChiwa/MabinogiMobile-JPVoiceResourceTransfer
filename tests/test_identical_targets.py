import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(PACKAGE_DIR))
import transfer_jp_voice_resources as transfer  # noqa: E402


def fixture_clip():
    return SimpleNamespace(
        m_Channels=1,
        m_Frequency=44100,
        m_BitsPerSample=16,
        m_CompressionFormat=2,
        m_LoadType=1,
        m_Stream=None,
        m_Length=1.0,
        m_Resource=SimpleNamespace(m_Source="audio.resS", m_Offset=0, m_Size=8),
        save=mock.Mock(),
    )


def fixture_jp(clip, name="vo_Action_Nao_Test"):
    return transfer.JPClip(
        name, "voice/test", clip.m_Length, transfer.clip_signature(clip),
        b"exactFSB", "audio.resS", "AssetPackages/0010.blob", 0, 1,
    )


class IdenticalResourceValidationTests(unittest.TestCase):
    def setUp(self):
        self.clip = fixture_clip()
        self.jp = fixture_jp(self.clip)

    def test_exact_match_must_pass_complete_decoder_validation(self):
        result = {"duration": 1.0, "frames": 44100}
        with mock.patch.object(transfer, "validate_fsb5_payload", return_value=result) as decoder:
            self.assertEqual(transfer.validate_already_identical(self.clip, self.jp, b"exactFSB", 8), result)
        decoder.assert_called_once_with(
            b"exactFSB", expected_duration=1.0, expected_channels=1,
            expected_frequency=44100, max_bytes=8,
        )
        self.clip.save.assert_not_called()

    def test_byte_or_metadata_difference_is_not_accepted_as_noop(self):
        cases = (
            ("payload", b"otherFSB"),
            ("m_Channels", 2),
            ("m_Frequency", 48000),
            ("m_CompressionFormat", 1),
            ("m_BitsPerSample", 24),
            ("m_LoadType", 0),
            ("m_Stream", 1),
            ("m_Length", 0.999999),
        )
        for field, value in cases:
            with self.subTest(field=field):
                clip = fixture_clip()
                payload = b"exactFSB"
                if field == "payload":
                    payload = value
                else:
                    setattr(clip, field, value)
                with mock.patch.object(transfer, "validate_fsb5_payload") as decoder:
                    self.assertIsNone(transfer.validate_already_identical(clip, self.jp, payload, 8))
                    decoder.assert_not_called()

    def test_equal_but_corrupt_data_cannot_satisfy_the_target(self):
        with mock.patch.object(transfer, "validate_fsb5_payload", side_effect=ValueError("incomplete decode")):
            with self.assertRaisesRegex(ValueError, "incomplete decode"):
                transfer.validate_already_identical(self.clip, self.jp, b"exactFSB", 8)


class IdenticalPreparationTests(unittest.TestCase):
    def test_modified_bundle_uses_blob_hash_not_last_object_hash(self):
        # Regression for reusing original_hash in the per-object validation
        # loop: its final value used to falsely flag an unchanged source blob.
        with tempfile.TemporaryDirectory(prefix="voice-hash-regression-") as temp:
            root = Path(temp)
            source = root / "AssetPackages" / "0010.blob"
            source.parent.mkdir()
            original_bundle = b"original-bundle"
            updated_bundle = b"modified-bundle"
            source.write_bytes(original_bundle)
            clip = fixture_clip()
            jp = fixture_jp(clip)
            tw = transfer.TWClip(jp.name, jp.container, jp.signature, source, 0, 1, 8)
            target = SimpleNamespace(
                type=SimpleNamespace(name="AudioClip"), path_id=1,
                assets_file=SimpleNamespace(name="audio.assets"),
                byte_size=8, byte_start=0, data=None,
                read=lambda: clip, get_raw_data=lambda: b"old--obj",
            )
            clip.save.side_effect = lambda: setattr(target, "data", b"new--obj")
            other = SimpleNamespace(
                type=SimpleNamespace(name="MonoBehaviour"), path_id=2,
                assets_file=target.assets_file, get_raw_data=lambda: b"other-object",
            )
            env = SimpleNamespace(
                objects=[target, other],
                file=SimpleNamespace(files={
                    "audio.resS": SimpleNamespace(bytes=b"orig-FSB"),
                    "audio.assets": SimpleNamespace(reader=SimpleNamespace(bytes=b"old--obj")),
                }),
            )
            checked = SimpleNamespace(
                objects=[target, other],
                file=SimpleNamespace(files={
                    "audio.resS": SimpleNamespace(bytes=b"exactFSB"),
                    "audio.assets": SimpleNamespace(reader=SimpleNamespace(bytes=b"new--obj")),
                }),
            )
            with (
                mock.patch.object(transfer, "iter_unityfs_slices", return_value=iter([(0, original_bundle)])),
                mock.patch.object(transfer, "load_bundle", side_effect=[env, checked]),
                mock.patch.object(transfer, "parse_fsb5", return_value={"channels": 1, "frequency": 44100}),
                mock.patch.object(transfer, "validate_fsb5_payload", return_value={"duration": 1.0}),
                mock.patch.object(transfer, "save_bundle_preserving_unchanged_files", return_value=updated_bundle),
                mock.patch.object(transfer, "preserve_bundle_size", return_value=updated_bundle),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                reports, staged, hashes, _ = transfer.replace_matches_preserve_structure(
                    root, [{"tw": tw, "jp": jp, "match_kind": "exact"}],
                    object(), root / "stage",
                )
            self.assertEqual([item["status"] for item in reports], ["prepared"])
            self.assertEqual(source.read_bytes(), original_bundle)
            self.assertEqual(hashes, {source.resolve(): transfer.sha256_bytes(original_bundle)})
            self.assertEqual(staged[source.resolve()].read_bytes(), updated_bundle)
            self.assertEqual(set(staged), set(hashes))
            self.assertIn("source_payload_identical", reports[0])
            self.assertFalse(reports[0]["source_payload_identical"])
            self.assertNotEqual(reports[0]["original_resource_sha256"], reports[0]["new_resource_sha256"])

    def test_noop_only_blob_is_not_staged_or_added_to_commit_hashes(self):
        with tempfile.TemporaryDirectory(prefix="voice-identical-test-") as temp:
            root = Path(temp)
            source = root / "AssetPackages" / "0010.blob"
            source.parent.mkdir()
            source.write_bytes(b"original-bundle")
            clip = fixture_clip()
            jp = fixture_jp(clip)
            tw = transfer.TWClip(jp.name, jp.container, jp.signature, source, 0, 1, 8)
            obj = SimpleNamespace(
                type=SimpleNamespace(name="AudioClip"), path_id=1,
                assets_file=SimpleNamespace(name="audio.assets"),
                read=lambda: clip, get_raw_data=lambda: b"original-object",
            )
            env = SimpleNamespace(
                objects=[obj],
                file=SimpleNamespace(files={"audio.resS": SimpleNamespace(bytes=b"exactFSB")}),
            )
            # Mock the real imported module's attribute. Installing only a
            # fake child in sys.modules can leave a real parent's import
            # attributes inconsistent when test order changes.
            from UnityPy import streams
            with (
                mock.patch.object(streams, "EndianBinaryWriter") as writer,
                mock.patch.object(transfer, "iter_unityfs_slices", return_value=iter([(0, b"original-bundle")])),
                mock.patch.object(transfer, "load_bundle", return_value=env),
                mock.patch.object(transfer, "validate_fsb5_payload", return_value={"duration": 1.0}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                reports, staged, hashes, warnings = transfer.replace_matches_preserve_structure(
                    root, [{"tw": tw, "jp": jp, "match_kind": "exact"}],
                    object(), root / "stage",
                )
            self.assertEqual([item["status"] for item in reports], ["already_identical"])
            self.assertIn("source_payload_identical", reports[0])
            self.assertTrue(reports[0]["source_payload_identical"])
            self.assertEqual(reports[0]["original_resource_sha256"], reports[0]["new_resource_sha256"])
            self.assertFalse(reports[0]["write_performed"])
            self.assertEqual(staged, {})
            self.assertEqual(hashes, {})
            self.assertEqual(warnings, [])
            self.assertEqual(source.read_bytes(), b"original-bundle")
            self.assertFalse((root / "stage").exists())
            clip.save.assert_not_called()
            writer.assert_not_called()
            self.assertEqual(transfer.commit_staged_blob_batch(root, staged, hashes), [])
            self.assertFalse((root / "backup-manifest.json").exists())


class IdenticalMainReportTests(unittest.TestCase):
    def run_fixture(self, statuses, commit_error=None, preserve_structure=True):
        temp = tempfile.TemporaryDirectory(prefix="voice-identical-report-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        tw_root = root / "tw"
        package_dir = tw_root / "appdata" / "AssetPackages"
        package_dir.mkdir(parents=True)
        (package_dir / "0010.blob").write_bytes(b"source-unchanged")
        output_root = root / "patched"
        args = SimpleNamespace(
            jp_root=None, cache_dir=PACKAGE_DIR, tw_root=tw_root,
            output_copy=output_root, dry_run=False, max_clips=0,
            include=[], blob=[], priority=None, force=True, hardlink_copy=False,
            preserve_structure=preserve_structure, no_progress=True, report_dir=None,
        )
        clip = fixture_clip()
        targets = []
        jp_by_name = {}
        reports = []
        for index, status in enumerate(statuses, 1):
            name = f"vo_Action_Nao_Test{index}"
            jp = fixture_jp(clip, name)
            targets.append(transfer.TWClip(name, jp.container, jp.signature, package_dir / "0010.blob", 0, index, 8))
            jp_by_name[name.casefold()] = [jp]
            reports.append({
                "name": name, "container": jp.container, "status": status,
                "blob": "AssetPackages/0010.blob", "bundle_offset": 0,
                "path_id": index, "reason": "injected fixture",
                "source_payload_identical": status in {"already_identical", "metadata_adjusted"},
            })
        index_module = types.ModuleType("resource_voice_index")
        index_module.get_index = mock.Mock(return_value={})
        index_module.verify_files = mock.Mock()
        stats = {"engine_versions": ["2021.3.0"]}
        commit = mock.Mock(side_effect=commit_error, return_value=[])
        with (
            mock.patch.object(transfer, "parse_args", return_value=args),
            mock.patch.object(transfer, "assert_not_running"),
            mock.patch.object(transfer, "load_unitypy", return_value=object()),
            mock.patch.object(transfer, "targets_from_index", return_value=(targets, stats)),
            mock.patch.object(transfer, "load_resource_cache", return_value=({}, jp_by_name, stats)),
            mock.patch.object(transfer, "replace_matches", return_value=(reports, {}, {}, []) if preserve_structure else (reports, [], [])),
            mock.patch.object(transfer, "commit_staged_blob_batch", commit),
            mock.patch.dict(sys.modules, {"resource_voice_index": index_module}),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = transfer.main()
        if not statuses and not preserve_structure:
            self.assertFalse(output_root.exists(), "No matching targets must not create a game copy")
            return result, {"transaction_status": "preflight_failed"}, "", commit
        report = json.loads((output_root / "appdata" / "patch-report.json").read_text(encoding="utf-8"))
        summary = (output_root / "appdata" / "replacement-summary.txt").read_text(encoding="utf-8")
        self.assertEqual((package_dir / "0010.blob").read_bytes(), b"source-unchanged")
        return result, report, summary, commit

    def test_all_identical_batch_succeeds_without_claiming_any_written_clip(self):
        result, report, summary, commit = self.run_fixture(["already_identical", "already_identical"])
        self.assertEqual(result, 0, report)
        self.assertEqual(report["committed_count"], 0)
        self.assertEqual(report["prepared_count"], 0)
        self.assertEqual(report["already_identical_count"], 2)
        self.assertEqual(report["satisfied_count"], 2)
        self.assertEqual(report["unchanged_source_count"], 2)
        self.assertIn("Replaced clips: 0", summary)
        self.assertIn("Already identical and fully verified (no rewrite): 2", summary)
        self.assertIn("Source audio identical to original TW payload: 2", summary)
        commit.assert_called_once()

    def test_full_rebuild_reports_source_equality_separately_from_metadata_changes(self):
        result, report, summary, commit = self.run_fixture(
            ["already_identical", "metadata_adjusted", "replaced"], preserve_structure=False,
        )
        self.assertEqual(result, 0, report)
        self.assertEqual(report["committed_count"], 2)
        self.assertEqual(report["audio_replaced_count"], 1)
        self.assertEqual(report["already_identical_count"], 1)
        self.assertEqual(report["metadata_adjusted_count"], 1)
        self.assertEqual(report["unchanged_source_count"], 2)
        self.assertEqual(report["satisfied_count"], 3)
        self.assertIn("Source audio identical to original TW payload: 2", summary)
        commit.assert_not_called()

    def test_empty_full_rebuild_is_rejected_before_copying(self):
        result, report, _, _ = self.run_fixture([], preserve_structure=False)
        self.assertEqual(result, 2)
        self.assertEqual(report["transaction_status"], "preflight_failed")

    def test_mixed_batch_counts_only_actual_replacements_as_committed(self):
        result, report, _, _ = self.run_fixture(["already_identical", "prepared"])
        self.assertEqual(result, 0, report)
        self.assertEqual(report["committed_count"], 1)
        self.assertEqual(report["already_identical_count"], 1)
        self.assertEqual(report["satisfied_count"], 2)
        self.assertEqual([item["status"] for item in report["replacements"]], ["already_identical", "replaced"])

    def test_quality_skip_commits_other_replacements_and_is_reported_explicitly(self):
        result, report, summary, commit = self.run_fixture(
            ["already_identical", "prepared", "skipped_quality"]
        )
        self.assertEqual(result, 0, report)
        self.assertEqual(report["transaction_status"], "committed_with_skips")
        self.assertEqual(report["committed_count"], 1)
        self.assertEqual(report["already_identical_count"], 1)
        self.assertEqual(report["satisfied_count"], 2)
        self.assertEqual(report["skipped_count"], 1)
        self.assertEqual(report["skipped_quality_count"], 1)
        self.assertEqual(report["processed_count"], 3)
        self.assertEqual(
            [item["status"] for item in report["replacements"]],
            ["already_identical", "replaced", "skipped_quality"],
        )
        self.assertIn("Skipped quality/capacity checks", summary)
        self.assertIn("original TW resource was retained", summary)
        commit.assert_called_once()

    def test_all_quality_skips_are_a_successful_no_write_batch(self):
        result, report, summary, commit = self.run_fixture(
            ["skipped_quality", "skipped_quality"]
        )
        self.assertEqual(result, 0, report)
        self.assertEqual(report["transaction_status"], "committed_with_skips")
        self.assertEqual(report["committed_count"], 0)
        self.assertEqual(report["satisfied_count"], 0)
        self.assertEqual(report["skipped_count"], 2)
        self.assertEqual(report["skipped_quality_count"], 2)
        self.assertEqual(report["processed_count"], 2)
        self.assertIn("Skipped quality/capacity checks", summary)
        commit.assert_called_once()

    def test_real_failure_still_blocks_a_batch_containing_quality_skip(self):
        result, report, _, commit = self.run_fixture(
            ["prepared", "skipped_quality", "failed"]
        )
        self.assertEqual(result, 2)
        self.assertEqual(report["transaction_status"], "preflight_failed")
        self.assertEqual(report["skipped_quality_count"], 1)
        self.assertEqual(report["processed_count"], 1)
        self.assertEqual(report["committed_count"], 0)
        self.assertEqual(
            [item["status"] for item in report["replacements"]],
            ["not_committed", "skipped_quality", "failed"],
        )
        commit.assert_not_called()

    def test_preflight_failure_keeps_verified_noop_and_does_not_commit_prepared(self):
        result, report, _, commit = self.run_fixture(["already_identical", "prepared", "failed"])
        self.assertEqual(result, 2)
        self.assertEqual(report["transaction_status"], "preflight_failed")
        self.assertEqual(report["prepared_count"], 1)
        self.assertEqual(report["committed_count"], 0)
        self.assertEqual(report["satisfied_count"], 1)
        self.assertEqual([item["status"] for item in report["replacements"]], ["already_identical", "not_committed", "failed"])
        commit.assert_not_called()

    def test_commit_failure_preserves_noop_status_without_claiming_written_audio(self):
        result, report, _, _ = self.run_fixture(["already_identical", "prepared"], RuntimeError("injected commit failure"))
        self.assertEqual(result, 2)
        self.assertEqual(report["transaction_status"], "commit_failed_before_mutation")
        self.assertEqual(report["committed_count"], 0)
        self.assertEqual(report["already_identical_count"], 1)
        self.assertEqual(report["satisfied_count"], 1)
        self.assertEqual([item["status"] for item in report["replacements"]], ["already_identical", "not_committed"])


if __name__ == "__main__":
    unittest.main()
