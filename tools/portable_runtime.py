"""Shared paths, worker dispatch and offline diagnostics for the portable app."""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave


def package_root() -> Path:
    """Keep user caches/logs beside the executable, outside frozen internals."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def worker_command(root: Path, arguments: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", *arguments]
    return [sys.executable, "-u", "-X", "utf8", str(root / "transfer_jp_voice_resources.py"), *arguments]


def terminate_worker_tree(process: subprocess.Popen) -> None:
    """Stop this live worker's tree before orphaned children retain its pipe.

    Windows multiprocessing descendants inherit stdout. Terminating just the
    parent leaves the GUI reader waiting forever, so taskkill must resolve the
    owned tree while that parent still exists. An already exited parent is
    skipped to avoid targeting a reused PID.
    """
    if process.poll() is not None:
        return
    pid = process.pid
    if type(pid) is not int or pid <= 0:
        return
    if sys.platform == "win32":
        taskkill = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "taskkill.exe"
        try:
            completed = subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=5,
            )
            if completed.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            try:
                process.kill()
            except OSError:
                pass


def _dependency_probe(task: int) -> dict:
    import UnityPy
    import astc_encoder
    from archspec.cpu import host
    from fmod_toolkit.importer import import_pyfmodex
    system = import_pyfmodex().System()
    try:
        version = system.version
    finally:
        system.release()
    return {"task": task, "unitypy": UnityPy.__version__, "fmod": version,
            "astc": astc_encoder.__version__, "cpu": str(host())}


def self_check() -> int:
    """No network or client reads; exercise all native runtime boundaries."""
    try:
        import tkinter
        from concurrent.futures import ProcessPoolExecutor
        import fmod_toolkit
        from ima_fsb_encoder import encode_pcm
        window = tkinter.Tk()
        try:
            window.withdraw()
            window.update()
            tk_version = window.tk.eval("info patchlevel")
        finally:
            window.destroy()
        pcm = struct.pack("<128h", *(round(8000 * math.sin(i * .2)) for i in range(128)))
        bank = encode_pcm(pcm, 1, 8000)
        samples = fmod_toolkit.raw_to_wav(bank, "runtime-check", 1, 8000)
        if not samples:
            raise RuntimeError("Native FSB5 decoder returned no samples")
        with wave.open(io.BytesIO(next(iter(samples.values()))), "rb") as decoded:
            frames = decoded.getnframes()
            if (frames, decoded.getnchannels(), decoded.getframerate()) != (128, 1, 8000):
                raise RuntimeError("Native FSB5 decoder returned unexpected audio metadata")
        with ProcessPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_dependency_probe, [1, 2]))
        print(json.dumps({"ok": True, "frozen": bool(getattr(sys, "frozen", False)),
                          "python": sys.version.split()[0], "tk": tk_version,
                          "decoded_frames": frames, "multiprocessing_tasks": len(results),
                          "workers": results}, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2


def install_source_dependencies(root: Path) -> int:
    """Developer/source fallback installs into its own venv, never system Python."""
    environment = root / ".venv"
    interpreter = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not interpreter.is_file():
        code = subprocess.call([sys.executable, "-m", "venv", str(environment)])
        if code:
            return code
    command = [str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
               "--only-binary=:all:", "--timeout", "20", "--retries", "2",
               "-r", str(root / "requirements-runtime.lock")]
    wheelhouse = root / "wheelhouse"
    if wheelhouse.is_dir():
        command.extend(["--no-index", "--find-links", str(wheelhouse)])
    else:
        # PyPI 0.2.4 has only an sdist; its code is pure Python. Native packages
        # retain the wheels-only rule rather than compiling on users' machines.
        command.append("--no-binary=tpk_ar")
    return subprocess.call(command, cwd=root)
