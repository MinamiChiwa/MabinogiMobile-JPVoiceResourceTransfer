import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
launcher = importlib.import_module("resource_transfer_launcher")


class LauncherOutputDirectoryTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.ttk = ttk
        self.temporary = tempfile.TemporaryDirectory(prefix="voice-gui-output-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.toolkit = self.root / "toolkit"
        self.toolkit.mkdir()
        self.source = self.root / "MabinogiMobile_TW-back"
        (self.source / "appdata").mkdir(parents=True)
        self.jp = self.root / "JP"
        self.jp.mkdir()
        try:
            self.window = tk.Tk()
            self.window.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.addCleanup(self.window.destroy)

    def exercise_gui(self, callback, browse_path=None, language="en"):
        self.notifications = []
        self.commands = []
        self.dialog_options = []

        def choose(**kwargs):
            self.dialog_options.append(kwargs)
            return str(browse_path)

        def notify(_title, text, **kwargs):
            self.notifications.append(text)

        def worker_command(_root, arguments):
            self.commands.append(arguments)
            return ["fake-worker"]

        def mainloop():
            frame = self.window.winfo_children()[0]
            widgets = frame.winfo_children()
            self.entries = {int(widget.grid_info()["row"]): widget
                            for widget in widgets if isinstance(widget, self.ttk.Entry)
                            and int(widget.grid_info()["row"]) in (1, 2, 3)}
            self.entries[1].insert(0, str(self.jp))
            self.entries[2].insert(0, str(self.source))
            self.browse = next(widget for widget in widgets
                               if isinstance(widget, self.ttk.Button)
                               and int(widget.grid_info()["row"]) == 3)
            row = next(widget for widget in widgets if isinstance(widget, self.ttk.Frame))
            labels = {"en": "Start transfer", "zh-Hans": "开始替换", "zh-Hant": "開始替換"}
            self.start = next(widget for widget in row.winfo_children()
                              if isinstance(widget, self.ttk.Button)
                              and widget.cget("text") == labels[language])
            callback()

        with mock.patch.object(self.tk, "Tk", return_value=self.window), \
             mock.patch.object(self.window, "mainloop", mainloop), \
             mock.patch("tkinter.filedialog.askdirectory", side_effect=choose), \
             mock.patch("tkinter.messagebox.showerror", side_effect=notify), \
             mock.patch("tkinter.messagebox.showinfo", side_effect=notify), \
             mock.patch.object(launcher, "worker_command", side_effect=worker_command), \
             mock.patch.object(launcher.threading, "Thread"):
            launcher.run_gui(self.toolkit, language)

    def test_browse_accepts_a_new_exact_launcher_folder(self):
        destination = self.root / "MabinogiMobile_TW"

        def actions():
            self.browse.invoke()
            self.assertEqual(self.entries[3].get(), str(destination))
            self.start.invoke()

        self.exercise_gui(actions, destination)
        self.assertEqual(self.dialog_options[0].get("mustexist"), False)
        self.assertEqual(len(self.commands), 1)
        args = self.commands[0]
        self.assertEqual(args[args.index("--output-copy") + 1], str(destination))
        self.assertEqual(self.notifications, [])
        self.assertFalse(destination.exists())

    def test_manual_empty_exact_launcher_folder_starts_without_renaming(self):
        destination = self.root / "MabinogiMobile_TW"
        destination.mkdir()

        def actions():
            self.entries[3].insert(0, str(destination))
            self.start.invoke()

        self.exercise_gui(actions)
        self.assertEqual(len(self.commands), 1)
        args = self.commands[0]
        self.assertEqual(args[args.index("--output-copy") + 1], str(destination))
        self.assertEqual(self.notifications, [])
        self.assertEqual(list(destination.iterdir()), [])

    def test_browse_nonempty_folder_keeps_selected_path_and_explains_contents(self):
        destination = self.root / "MabinogiMobile_TW"
        (destination / "appdata").mkdir(parents=True)

        def actions():
            self.browse.invoke()
            self.assertEqual(self.entries[3].get(), str(destination))

        self.exercise_gui(actions, destination)
        self.assertEqual(self.commands, [])
        self.assertEqual(len(self.notifications), 1)
        self.assertIn(str(destination), self.notifications[0])
        self.assertIn("appdata", self.notifications[0])
        self.assertIn("backup", self.notifications[0])
        self.assertTrue((destination / "appdata").is_dir())

    def test_manual_source_descendant_is_rejected_before_launch(self):
        destination = self.source / "empty-output"
        destination.mkdir()

        def actions():
            self.entries[3].insert(0, str(destination))
            self.start.invoke()

        self.exercise_gui(actions)
        self.assertEqual(self.commands, [])
        self.assertEqual(len(self.notifications), 1)
        self.assertIn(str(destination), self.notifications[0])
        self.assertIn(str(self.source), self.notifications[0])
        self.assertEqual(list(destination.iterdir()), [])

    def test_appdata_selection_explicitly_displays_the_actual_game_root(self):
        destination = self.root / "MabinogiMobile_TW"
        destination.mkdir()

        def actions():
            self.browse.invoke()
            self.assertEqual(self.entries[3].get(), str(destination))
            self.start.invoke()

        self.exercise_gui(actions, destination / "appdata")
        self.assertEqual(len(self.notifications), 1)
        self.assertIn(str(destination), self.notifications[0])
        self.assertIn("game root", self.notifications[0])
        args = self.commands[0]
        self.assertEqual(args[args.index("--output-copy") + 1], str(destination))

    def test_each_language_reports_nonempty_exact_destination_before_worker(self):
        destination = self.root / "MabinogiMobile_TW"
        (destination / "appdata").mkdir(parents=True)
        for language in ("zh-Hans", "zh-Hant", "en"):
            with self.subTest(language=language):
                if self.window.winfo_children():
                    self.window.winfo_children()[0].destroy()

                def actions():
                    self.entries[3].insert(0, str(destination))
                    self.start.invoke()

                self.exercise_gui(actions, language=language)
                self.assertEqual(self.commands, [])
                self.assertEqual(len(self.notifications), 1)
                self.assertIn(str(destination), self.notifications[0])
                self.assertIn("appdata", self.notifications[0])


if __name__ == "__main__":
    unittest.main()
