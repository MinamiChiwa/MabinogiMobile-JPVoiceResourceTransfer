import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    progress = importlib.import_module("resource_progress")
except ModuleNotFoundError:
    progress = None


class Variable:
    def set(self, value):
        self.value = value


class Progressbar:
    def __init__(self):
        self.options = {"mode": "indeterminate", "maximum": 100, "value": 0}
        self.animated = True
        self.stops = 0

    def configure(self, **options):
        self.options.update(options)

    def stop(self):
        self.stops += 1
        self.animated = False
        self.options["value"] = 0


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(progress, "Structured, weighted resource progress is missing")

    def event(self, stage, current, total, **kwargs):
        return progress.ProgressEvent(stage, current, total, **kwargs)

    def test_completed_scans_use_only_twenty_percent_of_overall_budget(self):
        state = progress.ProgressState()
        token = state.begin()
        state.apply(self.event("scan_tw", 1000, 1000, complete=True), token)
        self.assertEqual(state.overall, 10)
        state.apply(self.event("scan_jp", 900, 1000), token)
        self.assertEqual(state.overall, 19)
        state.apply(self.event("scan_jp", 901, 1000, complete=True), token)
        self.assertEqual(state.overall, 20)
        self.assertEqual((state.event.current, state.event.total), (901, 1000))

    def test_unknown_total_is_a_stage_boundary_and_never_claims_completion(self):
        state = progress.ProgressState()
        token = state.begin()
        state.apply(self.event("copy", 2, None, unit="files"), token)
        self.assertEqual(state.overall, 20)
        self.assertIsNone(state.event.total)
        state.apply(self.event("finalize", 0, None), token)
        state.apply(self.event("done", 1, 1, complete=True), token)
        self.assertEqual(state.overall, 99)
        self.assertTrue(state.active)

    def test_progress_never_regresses_when_legacy_scan_lines_follow_json(self):
        state = progress.ProgressState()
        token = state.begin()
        state.apply(self.event("replace", 2, 4), token)
        before = state.overall
        self.assertFalse(state.apply(self.event("scan_tw", 999, 1000), token))
        self.assertEqual(state.overall, before)
        self.assertEqual(state.event.stage, "replace")

    def test_protocol_roundtrip_and_malformed_events_are_ignored(self):
        event = self.event("replace", 2, 3, unit="blobs", operation="verify")
        self.assertEqual(progress.parse_progress_line(progress.format_progress(event)), event)
        for line in ("RESOURCE_TRANSFER_PROGRESS nope", "RESOURCE_TRANSFER_PROGRESS []",
                     'RESOURCE_TRANSFER_PROGRESS {"format":1,"stage":"replace","current":-1,"total":3}',
                     'RESOURCE_TRANSFER_PROGRESS {"format":1,"stage":"unknown","current":0,"total":1}'):
            self.assertIsNone(progress.parse_progress_line(line))

    def test_legacy_full_rebuild_line_is_an_explicit_replacement_event(self):
        parser = progress.ProgressParser()
        parser.parse("Preparing 3 blob batches.")
        starting = parser.parse("Preparing blob 2/3: AssetPackages/a.blob")
        self.assertEqual((starting.stage, starting.current, starting.total), ("replace", 1, 3))
        completed = parser.parse("Patched and verified: AssetPackages/a.blob (2 selected objects)")
        self.assertEqual((completed.stage, completed.current, completed.total), ("replace", 2, 3))

    def controller(self):
        self.bar = Progressbar()
        self.text, self.status = Variable(), Variable()
        self.popups = []
        labels = {stage: stage for stage in progress.STAGE_BUDGETS}
        messages = {"progress": "Overall {overall}% | {phase} {counter}",
                    "progress_start": "starting", "progress_done": "complete", "failed": "failed",
                    "stopped": "stopped", "done": "done", "warning": "source unchanged: {count}",
                    "failure_detail": "failed: {reason}"}
        return progress.GuiProgressController(self.bar, self.text, self.status, labels, messages,
                                              lambda text, warning: self.popups.append((text, warning)))

    def report(self, **fields):
        report = {"transaction_status": "committed", "replacements": [{"status": "replaced"}]}
        report.update(fields)
        return report

    def test_success_stops_animation_sets_static_full_bar_and_shows_one_popup(self):
        gui = self.controller()
        token = gui.begin()
        gui.update(self.event("replace", 2, 3), token)
        result = gui.finish(0, False, self.report(), token)
        self.assertTrue(result.success)
        self.assertEqual(self.bar.options, {"mode": "determinate", "maximum": 100, "value": 100})
        self.assertFalse(self.bar.animated)
        self.assertEqual(self.text.value, "complete")
        self.assertEqual(len(self.popups), 1)
        gui.finish(0, False, self.report(), token)
        gui.update(self.event("scan_tw", 0, None), token)
        self.assertEqual(len(self.popups), 1)
        self.assertEqual(self.bar.options["value"], 100)
        self.assertEqual(self.text.value, "complete")
        self.assertFalse(self.bar.animated)

    def test_previous_run_updates_cannot_change_a_new_run(self):
        gui = self.controller()
        old = gui.begin()
        gui.finish(2, False, None, old)
        new = gui.begin()
        gui.update(self.event("copy", 1, 2), new)
        before = dict(self.bar.options), self.text.value
        gui.update(self.event("done", 1, 1), old)
        gui.finish(0, False, self.report(), old)
        self.assertEqual((self.bar.options, self.text.value), before)
        self.assertEqual(self.popups, [])

    def test_failure_cancellation_and_invalid_reports_never_show_success(self):
        cases = [(2, False, self.report()), (0, True, self.report()), (0, False, None),
                 (0, False, self.report(transaction_status="failed")),
                 (0, False, self.report(replacements=[{"status": "failed"}])),
                 (0, False, self.report(failed=[{"reason": "bad"}]))]
        for code, cancelled, report in cases:
            with self.subTest(code=code, cancelled=cancelled, report=report):
                gui = self.controller()
                token = gui.begin()
                gui.update(self.event("done", 1, 1), token)
                result = gui.finish(code, cancelled, report, token)
                self.assertFalse(result.success)
                self.assertFalse(self.bar.animated)
                self.assertLess(self.bar.options["value"], 100)
                self.assertEqual(self.popups, [])

    def test_committed_source_limitations_show_a_warning_and_honest_count(self):
        gui = self.controller()
        token = gui.begin()
        result = gui.finish(0, False, self.report(unchanged_source_count=3), token)
        self.assertTrue(result.success)
        self.assertEqual(self.popups, [("done\nsource unchanged: 3", True)])
        self.assertIn("source unchanged: 3", self.status.value)

    def test_report_loader_rejects_missing_or_corrupt_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patch-report.json"
            self.assertIsNone(progress.load_completion_report(path))
            path.write_text("{", encoding="utf-8")
            self.assertIsNone(progress.load_completion_report(path))
            path.write_text(json.dumps(self.report()), encoding="utf-8")
            self.assertEqual(progress.load_completion_report(path), self.report())

    def test_known_operation_uses_a_distinct_stage_description(self):
        gui = self.controller()
        gui.labels["replace:verify"] = "verifying rebuilt bundles"
        token = gui.begin()
        gui.update(self.event("replace", 1, 3, operation="verify"), token)
        self.assertIn("verifying rebuilt bundles", self.text.value)

    def test_stage_counter_explains_whether_files_or_clips_are_counted(self):
        gui = self.controller()
        gui.messages.update(unit_files="files", unit_clips="clips")
        token = gui.begin()
        gui.update(self.event("scan_jp", 5, 9, unit="files"), token)
        self.assertIn("5/9 files", self.text.value)
        gui.update(self.event("scan_jp", 9, 9, unit="clips", operation="cache"), token)
        self.assertIn("9/9 clips", self.text.value)


if __name__ == "__main__":
    unittest.main()
