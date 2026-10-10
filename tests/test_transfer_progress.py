import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import transfer_jp_voice_resources as transfer


def events(output):
    prefix = "RESOURCE_TRANSFER_PROGRESS "
    return [json.loads(line[len(prefix):]) for line in output.splitlines() if line.startswith(prefix)]


class TransferProgressTests(unittest.TestCase):
    def test_copy_reports_real_completed_files_and_an_explicit_empty_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "appdata").mkdir(parents=True)
            (source / "appdata" / "one").write_bytes(b"one")
            (source / "two").write_bytes(b"two")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                transfer.copy_client(source, root / "copy")
            self.assertEqual((root / "copy" / "appdata" / "one").read_bytes(), b"one")
            copy = [item for item in events(output.getvalue()) if item["stage"] == "copy"]
            self.assertTrue(copy, "Copying currently gives the UI no progress events")
            self.assertEqual([(item["current"], item["total"]) for item in copy], [(0, 2), (1, 2), (2, 2)])
            self.assertTrue(copy[-1]["complete"])
            empty = root / "empty"
            empty.mkdir()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                transfer.copy_client(empty, root / "empty-copy")
            self.assertTrue(events(output.getvalue())[-1]["complete"])

    def test_failed_copy_never_emits_a_completed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            source.mkdir()
            target.mkdir()
            (target / "existing").write_text("preserve", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(ValueError):
                transfer.copy_client(source, target)
            self.assertFalse(any(item.get("complete") for item in events(output.getvalue())))

    def test_missing_source_cannot_emit_empty_copy_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(FileNotFoundError):
                transfer.copy_client(root / "missing", root / "target")
            self.assertFalse(any(item.get("complete") for item in events(output.getvalue())))


if __name__ == "__main__":
    unittest.main()
