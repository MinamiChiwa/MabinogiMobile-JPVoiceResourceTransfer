"""Cancel only the launched resource worker and its multiprocessing children."""
import ctypes
import importlib
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
runtime = importlib.import_module("portable_runtime")
launcher = importlib.import_module("resource_transfer_launcher")

POOL_WORKER = '''
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os

stop = None
def initialize(event):
    global stop
    stop = event
def wait_for_cancellation(task):
    print("child=" + str(os.getpid()), flush=True)
    stop.wait(30)
    return task
if __name__ == "__main__":
    multiprocessing.freeze_support()
    context = multiprocessing.get_context("spawn")
    event = context.Event()
    print("parent=" + str(os.getpid()), flush=True)
    with ProcessPoolExecutor(max_workers=2, mp_context=context, initializer=initialize, initargs=(event,)) as pool:
        list(pool.map(wait_for_cancellation, [1, 2]))
'''


def owned_cleanup(pid):
    subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                    "/PID", str(pid), "/T", "/F"], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)


class WorkerCancellationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows inherited-pipe process-tree regression")
    def test_cancel_processpool_closes_pipe_and_stops_every_owned_child(self):
        with tempfile.TemporaryDirectory(prefix="voice-cancel-pool-") as directory:
            script = Path(directory) / "pool.py"
            script.write_text(POOL_WORKER, encoding="utf-8")
            proc = subprocess.Popen([sys.executable, "-u", str(script)], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output = queue.Queue()
            eof = threading.Event()
            def reader():
                try:
                    for line in proc.stdout:
                        output.put(line.strip())
                finally:
                    eof.set()
            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            children = []
            handles = []
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.WaitForSingleObject.restype = ctypes.c_ulong
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            try:
                while len(children) < 2:
                    line = output.get(timeout=10)
                    if line.startswith("child="):
                        children.append(int(line.split("=", 1)[1]))
                for pid in children:
                    handle = kernel.OpenProcess(0x00100000, False, pid)
                    self.assertTrue(handle, f"Unable to hold child process handle for {pid}")
                    handles.append(handle)
                terminate = getattr(runtime, "terminate_worker_tree", lambda process: process.terminate())
                terminate(proc)
                proc.wait(timeout=10)
                self.assertTrue(eof.wait(2), "Parent-only cancellation leaves child stdout handles open and GUI stuck stopping")
                self.assertTrue(all(kernel.WaitForSingleObject(handle, 1000) == 0 for handle in handles),
                                "Every child belonging to the cancelled pool must stop")
            finally:
                if proc.poll() is None:
                    owned_cleanup(proc.pid)
                for pid, handle in zip(children, handles):
                    if kernel.WaitForSingleObject(handle, 0) != 0:
                        owned_cleanup(pid)
                for handle in handles:
                    kernel.CloseHandle(handle)
                proc.wait(timeout=10)
                thread.join(timeout=5)
                proc.stdout.close()

    def test_stop_requested_during_popen_cancels_the_just_created_worker_tree(self):
        spawn = getattr(launcher, "start_gui_worker", None)
        self.assertIsNotNone(spawn, "The immediate-stop Popen race needs the same process-tree cancellation")
        requested = {"value": False}
        holder = {"proc": None}
        proc = mock.Mock()
        proc.poll.return_value = None
        def popen(*args, **kwargs):
            requested["value"] = True
            return proc
        with mock.patch.object(launcher.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(launcher, "terminate_worker_tree") as terminate:
            result = spawn(["worker"], TOOLS, {}, holder, requested)
        self.assertIs(result, proc)
        self.assertIs(holder["proc"], proc)
        terminate.assert_called_once_with(proc)

    def test_taskkill_targets_the_owned_live_pid_before_parent_fallback(self):
        terminate = getattr(runtime, "terminate_worker_tree", None)
        self.assertIsNotNone(terminate, "Process tree termination helper is missing")
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = None
        def fail_taskkill(command, **options):
            self.assertEqual(command[-4:], ["/PID", "12345", "/T", "/F"])
            proc.terminate.assert_not_called()
            self.assertFalse(options.get("shell", False))
            self.assertEqual(options.get("creationflags"), getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return mock.Mock(returncode=1)
        with mock.patch.object(runtime.sys, "platform", "win32"), \
             mock.patch.object(runtime.subprocess, "run", side_effect=fail_taskkill):
            terminate(proc)
        proc.terminate.assert_called_once()

    def test_exited_worker_is_not_targeted_by_its_old_pid(self):
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = 0
        with mock.patch.object(runtime.subprocess, "run") as taskkill:
            runtime.terminate_worker_tree(proc)
        taskkill.assert_not_called()
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
