import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    output_paths = importlib.import_module("output_paths")
except ModuleNotFoundError:
    output_paths = None


class OutputDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(output_paths, "Output directory validation is not implemented")
        self.temporary = tempfile.TemporaryDirectory(prefix="voice-output-path-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def validate(self, path, sources=(), tool_root=None):
        return output_paths.validate_output_directory(path, sources, tool_root)

    def assert_error(self, code, path, sources=(), tool_root=None):
        with self.assertRaises(output_paths.OutputPathError) as caught:
            self.validate(path, sources, tool_root)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_existing_empty_launcher_folder_keeps_exact_name_and_location(self):
        destination = self.root / "MabinogiMobile_TW"
        destination.mkdir()
        self.assertEqual(self.validate(destination), destination)
        self.assertEqual(list(self.root.iterdir()), [destination])

    def test_new_launcher_folder_keeps_exact_name_and_is_not_created(self):
        destination = self.root / "MabinogiMobile_TW"
        self.assertEqual(self.validate(destination), destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_nonempty_game_root_reports_actual_children_without_modifying_them(self):
        destination = self.root / "MabinogiMobile_TW"
        destination.mkdir()
        (destination / "appdata").mkdir()
        (destination / "appdata" / "voice.blob").write_bytes(b"keep original game")
        (destination / ".hidden-index").write_bytes(b"hidden")
        if os.name == "nt":
            import ctypes
            self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(str(destination / ".hidden-index"), 2))
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        error = self.assert_error("nonempty", destination)
        self.assertEqual(error.path, destination)
        self.assertEqual(set(error.entries), {"appdata", ".hidden-index"})
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(set(destination.iterdir()), {destination / "appdata", destination / ".hidden-index"})

    def test_nonempty_details_are_bounded_to_five_real_child_names(self):
        for index in range(9):
            (self.root / f"file-{index}").touch()
        error = self.assert_error("nonempty", self.root)
        self.assertEqual(len(error.entries), 5)
        self.assertTrue(all((self.root / name).is_file() for name in error.entries))
        self.assertTrue(error.more_entries)

    def test_file_is_reported_as_not_a_directory(self):
        path = self.root / "MabinogiMobile_TW"
        path.write_bytes(b"not a directory")
        error = self.assert_error("not_directory", path)
        self.assertEqual(error.path, path)
        self.assertEqual(path.read_bytes(), b"not a directory")

    def test_new_path_under_a_file_is_rejected_before_worker(self):
        (self.root / "file").touch()
        self.assert_error("not_directory", self.root / "file" / "MabinogiMobile_TW")

    def test_directory_enumeration_permission_error_is_not_treated_as_empty(self):
        destination = self.root / "MabinogiMobile_TW"
        destination.mkdir()
        real_iterdir = Path.iterdir

        def denied(path):
            if path == destination:
                raise PermissionError("access denied")
            return real_iterdir(path)

        with mock.patch.object(Path, "iterdir", denied):
            self.assert_error("unreadable", destination)

    def test_source_and_destination_equality_is_rejected_even_when_empty(self):
        source = self.root / "MabinogiMobile_TW-back"
        source.mkdir()
        error = self.assert_error("source_overlap", source, [source])
        self.assertEqual(error.source, source)

    def test_empty_and_new_descendants_of_each_client_are_rejected(self):
        jp = self.root / "JP"
        tw = self.root / "MabinogiMobile_TW-back"
        jp.mkdir()
        tw.mkdir()
        for source in (jp, tw):
            empty = source / "empty"
            empty.mkdir()
            with self.subTest(source=source, kind="empty"):
                self.assert_error("source_overlap", empty, [jp, tw])
            with self.subTest(source=source, kind="new"):
                self.assert_error("source_overlap", source / "new", [jp, tw])

    def test_source_appdata_input_protects_the_outer_game_root(self):
        source = self.root / "MabinogiMobile_TW-back"
        appdata = source / "appdata"
        appdata.mkdir(parents=True)
        error = self.assert_error("source_overlap", source / "new", [appdata])
        self.assertEqual(error.source, source)

    def test_destination_appdata_normalizes_to_the_same_root_as_the_worker(self):
        destination = self.root / "MabinogiMobile_TW"
        destination.mkdir()
        self.assertEqual(self.validate(destination / "appdata"), destination)
        self.assertFalse((destination / "appdata").exists())

    def test_existing_appdata_cannot_bypass_outer_root_nonempty_guard(self):
        destination = self.root / "MabinogiMobile_TW"
        (destination / "appdata").mkdir(parents=True)
        error = self.assert_error("nonempty", destination / "appdata")
        self.assertEqual(error.path, destination)
        self.assertEqual(error.entries, ("appdata",))

    def test_file_named_appdata_is_not_silently_treated_as_parent(self):
        path = self.root / "appdata"
        path.touch()
        self.assert_error("not_directory", path)

    def test_empty_launcher_root_beside_backup_is_accepted(self):
        source = self.root / "MabinogiMobile_TW-back"
        (source / "appdata").mkdir(parents=True)
        destination = self.root / "MabinogiMobile_TW"
        destination.mkdir()
        self.assertEqual(self.validate(destination, [source]), destination)
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(list(source.iterdir()), [source / "appdata"])

    def test_tool_root_and_its_descendants_are_protected(self):
        toolkit = self.root / "toolkit"
        toolkit.mkdir()
        self.assert_error("tool_overlap", toolkit, tool_root=toolkit)
        self.assert_error("tool_overlap", toolkit / "output", tool_root=toolkit)

    def test_symlink_alias_cannot_bypass_source_protection(self):
        source = self.root / "MabinogiMobile_TW-back"
        source.mkdir()
        alias = self.root / "alias"
        try:
            alias.symlink_to(source, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlinks unavailable: {exc}")
        self.assert_error("source_overlap", alias, [source])
        self.assert_error("source_overlap", alias / "new", [source])

    def test_symlink_source_alias_protects_real_client_directory(self):
        source = self.root / "MabinogiMobile_TW-back"
        source.mkdir()
        alias = self.root / "alias"
        try:
            alias.symlink_to(source, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlinks unavailable: {exc}")
        self.assert_error("source_overlap", source / "new", [alias])


if __name__ == "__main__":
    unittest.main()
