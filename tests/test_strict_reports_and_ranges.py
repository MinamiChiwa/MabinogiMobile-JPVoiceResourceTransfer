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


PACKAGE_DIR = (
    Path(__file__).resolve().parents[1]
    / "release"
    / "MabinogiMobile_JPVoiceResourceTransfer_0.1.5"
)
sys.path.insert(0, str(PACKAGE_DIR))

import transfer_jp_voice_resources as transfer  # noqa: E402


class CommitFailureReportStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="voice-report-test-")
        self.root = Path(self.temp_dir.name)
        self.manifest_path = self.root / "backup-manifest.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_manifest(self, value: object) -> None:
        self.manifest_path.write_text(json.dumps(value), encoding="utf-8")

    def test_reports_confirmed_rollback_states(self) -> None:
        for manifest_status, expected in (
            ("rolled_back", "commit_failed_rolled_back"),
            ("rollback_incomplete", "commit_failed_rollback_incomplete"),
        ):
            with self.subTest(manifest_status=manifest_status):
                self.write_manifest({"transaction_status": manifest_status})
                self.assertEqual(
                    transfer.commit_failure_report_status(self.root), expected
                )

    def test_absent_manifest_means_commit_failed_before_mutation(self) -> None:
        self.assertEqual(
            transfer.commit_failure_report_status(self.root),
            "commit_failed_before_mutation",
        )

    def test_existing_manifest_blocks_commit_before_mutation(self) -> None:
        self.write_manifest({"transaction_status": "committed"})
        self.assertEqual(
            transfer.commit_failure_report_status(self.root, True),
            "commit_failed_before_mutation",
        )

    def test_malformed_or_unrecognized_manifest_has_unknown_status(self) -> None:
        self.manifest_path.write_text("{broken", encoding="utf-8")
        self.assertEqual(
            transfer.commit_failure_report_status(self.root),
            "commit_failed_rollback_unknown",
        )
        self.write_manifest({"transaction_status": "prepared"})
        self.assertEqual(
            transfer.commit_failure_report_status(self.root),
            "commit_failed_rollback_unknown",
        )


class VerifyChangesWithinRangesTests(unittest.TestCase):
    def test_allows_changes_within_approved_ranges(self) -> None:
        original = b"abcdefghij"
        updated = b"abXXefYYij"
        transfer.verify_changes_within_ranges(
            original, updated, [(2, 4), (6, 8)], "fixture"
        )

    def test_rejects_changes_outside_approved_ranges(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside target slots"):
            transfer.verify_changes_within_ranges(
                b"abcdefghij", b"Xbcdefghij", [(2, 4)], "fixture"
            )

    def test_rejects_length_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "changed length"):
            transfer.verify_changes_within_ranges(b"abc", b"abcd", [(1, 2)], "fixture")

    def test_rejects_overlapping_reversed_and_out_of_bounds_ranges(self) -> None:
        cases = (
            ([(1, 4), (3, 5)], "overlapping"),
            ([(3, 2)], "reversed"),
            ([(0, 5)], "out of bounds"),
        )
        for ranges, label in cases:
            with self.subTest(ranges=ranges), self.assertRaisesRegex(
                ValueError, "Invalid or overlapping"
            ):
                transfer.verify_changes_within_ranges(b"abcd", b"abcd", ranges, label)


class ZeroTargetPreflightReportTests(unittest.TestCase):
    def test_main_writes_a_failure_reason_when_no_targets_match(self) -> None:
        with tempfile.TemporaryDirectory(prefix="voice-zero-target-test-") as temp:
            temp_root = Path(temp)
            tw_root = temp_root / "tw"
            (tw_root / "appdata" / "AssetPackages").mkdir(parents=True)
            output_root = temp_root / "patched"
            args = SimpleNamespace(
                jp_root=None,
                cache_dir=PACKAGE_DIR,
                tw_root=tw_root,
                output_copy=output_root,
                dry_run=False,
                max_clips=5,
                include=[],
                blob=[],
                priority=None,
                force=True,
                hardlink_copy=False,
                preserve_structure=True,
                no_progress=True,
                report_dir=None,
            )
            index_module = types.ModuleType("resource_voice_index")
            index_module.get_index = mock.Mock(return_value={})
            index_module.verify_files = mock.Mock()
            stats = {"engine_versions": []}
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with (
                mock.patch.object(transfer, "parse_args", return_value=args),
                # This test uses a fully mocked source/index/transformer. A
                # game or launcher process on the host must not prevent the
                # fixture from reaching the zero-target reporting branch.
                mock.patch.object(transfer, "assert_not_running"),
                mock.patch.object(transfer, "load_unitypy", return_value=object()),
                mock.patch.object(
                    transfer, "targets_from_index", return_value=([], stats)
                ),
                mock.patch.object(
                    transfer,
                    "load_resource_cache",
                    return_value=({}, {}, {"engine_versions": []}),
                ),
                mock.patch.object(transfer, "replace_matches", return_value=([], {}, {}, [])),
                mock.patch.dict(sys.modules, {"resource_voice_index": index_module}),
                contextlib.redirect_stdout(captured_stdout),
                contextlib.redirect_stderr(captured_stderr),
            ):
                result = transfer.main()

            report_path = output_root / "appdata" / "patch-report.json"
            self.assertTrue(
                report_path.is_file(),
                f"Missing zero-target report; exit={result}; stdout={captured_stdout.getvalue()!r}; stderr={captured_stderr.getvalue()!r}",
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(result, 2)
            self.assertEqual(report["transaction_status"], "preflight_failed")
            self.assertTrue(
                any(
                    item.get("status") == "failed"
                    and "no TW voice targets matched" in item.get("reason", "")
                    for item in report["replacements"]
                )
            )


if __name__ == "__main__":
    unittest.main()
