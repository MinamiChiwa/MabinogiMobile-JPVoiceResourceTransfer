import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


LAUNCHER_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "resource_transfer_launcher.py"
)
sys.path.insert(0, str(LAUNCHER_PATH.parent))
spec = importlib.util.spec_from_file_location("launcher_progress", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class TerminalCapture(io.StringIO):
    def isatty(self):
        return True


class ConsoleStatusTests(unittest.TestCase):
    def test_refresh_overwrites_one_row_and_clears_shorter_chinese_status(self):
        stream = TerminalCapture()
        console = launcher.ConsoleStatus(stream)
        with mock.patch.object(console, "columns", return_value=79):
            console.update("正在处理资源")
            console.update("完成")
        self.assertEqual(stream.getvalue(), "\r正在处理资源\r完成" + " " * 8)
        self.assertNotIn("\n", stream.getvalue())

    def test_width_limit_respects_cjk_and_combining_characters(self):
        text = "正在处理资源 e\u0301 audio"
        for columns in range(1, 22):
            with self.subTest(columns=columns):
                fitted = launcher.fit_console_line(text, columns)
                self.assertLessEqual(launcher.console_width(fitted), columns)
        self.assertEqual(launcher.console_width("声道 e\u0301"), 6)

    def test_console_leaves_last_cell_empty_to_avoid_wrapping(self):
        console = launcher.ConsoleStatus(TerminalCapture())
        with mock.patch.object(launcher.shutil, "get_terminal_size", return_value=os.terminal_size((80, 24))):
            self.assertEqual(console.columns(), 79)

    def test_regular_output_and_finish_clear_the_status_and_end_with_newline(self):
        stream = TerminalCapture()
        console = launcher.ConsoleStatus(stream)
        with mock.patch.object(console, "columns", return_value=79):
            console.update("处理中")
            console.write_output("ERROR: test")
            console.update("结束")
            console.write_output("Completed\n")
        self.assertIn("\r" + " " * 6 + "\rERROR: test\n", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\r" + " " * 4 + "\rCompleted\n"))

    def test_exception_clears_status_before_launcher_reports_error(self):
        stream = TerminalCapture()
        console = launcher.ConsoleStatus(stream)
        with mock.patch.object(console, "columns", return_value=79):
            with self.assertRaises(RuntimeError):
                with console:
                    console.update("处理中")
                    raise RuntimeError("test")
        self.assertTrue(stream.getvalue().endswith("\r" + " " * 6 + "\r"))

    def test_redirected_output_contains_plain_lines_without_cursor_controls(self):
        stream = io.StringIO()
        console = launcher.ConsoleStatus(stream)
        console.update("处理中")
        console.write_output("diagnostic\n")
        console.update("完成")
        self.assertEqual(stream.getvalue(), "处理中\ndiagnostic\n完成\n")


class LauncherLanguageTests(unittest.TestCase):
    def test_all_supported_languages_have_core_guidance(self):
        for language in ("zh-Hans", "zh-Hant", "en"):
            with self.subTest(language=language):
                self.assertIn("jp_prompt", launcher.MESSAGES[language])
                self.assertIn("tw_prompt", launcher.MESSAGES[language])
                self.assertIn("output_prompt", launcher.MESSAGES[language])
                self.assertIn("prefix_prompt", launcher.MESSAGES[language])

    def test_locale_detection_maps_simplified_traditional_and_other_to_expected_language(self):
        with mock.patch.dict(os.environ, {"LC_ALL": "zh_CN.UTF-8", "LANG": ""}, clear=False):
            self.assertEqual(launcher.detect_language(), "zh-Hans")
        with mock.patch.dict(os.environ, {"LC_ALL": "zh_TW.UTF-8", "LANG": ""}, clear=False):
            self.assertEqual(launcher.detect_language(), "zh-Hant")
        with mock.patch.dict(os.environ, {"LC_ALL": "en_US.UTF-8", "LANG": ""}, clear=False):
            self.assertEqual(launcher.detect_language(), "en")


class LoggedProcessTests(unittest.TestCase):
    def run_worker(self, stream):
        temp = tempfile.TemporaryDirectory(prefix="voice-launcher-progress-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        log_path = root / "worker.log"
        worker = (
            "import sys, time; "
            "print('TW scan files 1/2', flush=True); "
            "time.sleep(0.15); "
            "print('TW scan files 2/2', flush=True); "
            "print('Preparing blob 1/1: test.blob', flush=True); "
            "print('Prepared structure-preserving blob: test.blob (1 bundles)', flush=True); "
            "print('ERROR: expected diagnostic', file=sys.stderr, flush=True); "
            "sys.stdout.write('last line without newline'); sys.stdout.flush(); "
            "sys.exit(2)"
        )
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        with contextlib.redirect_stdout(stream):
            code = launcher.run_logged_process(
                [sys.executable, "-u", "-c", worker], root, log_path,
                environment, "test header\n",
            )
        return code, stream.getvalue(), log_path.read_text(encoding="utf-8")

    def test_interactive_progress_is_replaced_but_all_worker_output_is_logged(self):
        code, output, log = self.run_worker(TerminalCapture())
        self.assertEqual(code, 2)
        self.assertNotIn("TW scan files", output)
        self.assertNotIn("Preparing blob 1/1", output)
        self.assertNotIn("Prepared structure-preserving blob", output)
        self.assertIn("ERROR: expected diagnostic\n", output)
        self.assertIn("last line without newline\n", output)
        for line in ("TW scan files 1/2", "TW scan files 2/2", "Preparing blob 1/1", "Prepared structure-preserving blob", "ERROR: expected diagnostic", "last line without newline"):
            self.assertIn(line, log)
        self.assertIn("[状态]", log)
        self.assertNotIn("\r", log)
        self.assertTrue(output.endswith("\n"))

    def test_redirected_worker_output_retains_progress_lines_and_exit_code(self):
        code, output, log = self.run_worker(io.StringIO())
        self.assertEqual(code, 2)
        self.assertIn("TW scan files 1/2\n", output)
        self.assertIn("Preparing blob 1/1: test.blob\n", output)
        self.assertNotIn("\r", output)
        self.assertNotIn("\x1b", output)
        self.assertIn("退出码 2", output)
        self.assertIn("ERROR: expected diagnostic", log)

    def test_structured_events_drive_cli_status_without_showing_json_payloads(self):
        import resource_progress
        with tempfile.TemporaryDirectory(prefix="voice-cli-protocol-") as directory:
            root = Path(directory)
            lines = [resource_progress.format_progress(resource_progress.ProgressEvent("scan_tw", 100, 100, unit="files", complete=True)),
                     resource_progress.format_progress(resource_progress.ProgressEvent("copy", 2, 4, unit="files")),
                     resource_progress.format_progress(resource_progress.ProgressEvent("replace", 1, 3, unit="blobs"))]
            worker = "import time; " + "; ".join("print(" + repr(line) + ", flush=True); time.sleep(.05)" for line in lines)
            stream = TerminalCapture()
            with contextlib.redirect_stdout(stream):
                code = launcher.run_logged_process([sys.executable, "-u", "-c", worker], root,
                                                  root / "log", dict(os.environ), "", "en")
            self.assertEqual(code, 0)
            self.assertNotIn(resource_progress.PROGRESS_PREFIX, stream.getvalue())
            self.assertIn("Copying TW client", stream.getvalue())
            self.assertIn("2/4", stream.getvalue())
            self.assertIn("65%", stream.getvalue())
            self.assertIn(resource_progress.PROGRESS_PREFIX, (root / "log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
