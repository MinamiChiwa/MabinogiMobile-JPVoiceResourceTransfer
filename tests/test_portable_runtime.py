"""Deployment boundaries: argv stays intact and bundled workers start correctly."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


class PortableRuntimeTests(unittest.TestCase):
    def runtime(self):
        try:
            return importlib.import_module("portable_runtime")
        except ImportError:
            self.fail("Portable runtime command/root handling is missing")

    def test_source_worker_preserves_non_ascii_and_shell_metacharacters(self):
        runtime = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "transfer_jp_voice_resources.py").write_text(
                "import sys,json; print(json.dumps(sys.argv[1:],ensure_ascii=False))", encoding="utf-8")
            args = ["--tw-root", "C:/游戏 目录 & test", "--include", "^Vo_.*"]
            completed = subprocess.run(runtime.worker_command(root, args), capture_output=True,
                                       text=True, encoding="utf-8", check=True)
            self.assertEqual(json.loads(completed.stdout), args)

    def test_frozen_worker_dispatches_executable_without_python_flags(self):
        runtime = self.runtime()
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", "C:/便携 路径/JPVoiceTransfer.exe"):
            self.assertEqual(runtime.worker_command(Path("irrelevant"), ["--help"]),
                             ["C:/便携 路径/JPVoiceTransfer.exe", "--worker", "--help"])
            self.assertEqual(runtime.package_root(), Path("C:/便携 路径/JPVoiceTransfer.exe").resolve().parent)

    def test_worker_help_is_engine_help_before_launcher_parser(self):
        completed = subprocess.run([sys.executable, str(TOOLS / "resource_transfer_launcher.py"),
                                    "--worker", "--help"], capture_output=True, text=True,
                                   encoding="utf-8", timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--tw-root", completed.stdout)

    def test_self_check_decodes_generated_audio_and_spawns_workers(self):
        completed = subprocess.run([sys.executable, str(TOOLS / "resource_transfer_launcher.py"),
                                    "--self-check"], capture_output=True, text=True,
                                   encoding="utf-8", timeout=45)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["decoded_frames"], 128)
        self.assertEqual(result["multiprocessing_tasks"], 2)


if __name__ == "__main__":
    unittest.main()
