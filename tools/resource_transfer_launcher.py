#!/usr/bin/env python3
"""Interactive Windows launcher; user paths never pass through cmd parsing."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def ask_path(message: str, required: bool = True) -> str:
    while True:
        value = input(message).strip().strip('"').strip()
        if value or not required:
            return value
        print("A path cannot be empty. Please enter it again.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("transfer", "scan", "dependencies"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    log_path = root / ("install_dependencies.log" if args.mode == "dependencies" else f"run_{args.mode}.log")
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError("需要 Python 3.11 或更新版本。")
        if args.mode == "dependencies":
            command = [sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")]
        else:
            print("JP path is optional: press Enter to use the bundled compressed resource cache.", flush=True)
            jp = ask_path("JP client root (optional, press Enter for bundled cache): ", required=False)
            if not jp and not all((root / name).is_file() for name in ("resource_cache.bin", "resource_cache.json")):
                raise RuntimeError("Bundled compressed resources are missing. Re-extract the package or provide a JP path.")
            if not jp:
                print("Using bundled compressed resource cache.", flush=True)
            tw = ask_path("TW game root (the folder containing appdata; appdata is also accepted): ")
            if args.mode == "transfer":
                output = ask_path("New output game-root directory (do not use the original client): ")
                limit_text = input("Maximum clips (Enter for 5, 0 for all): ").strip() or "5"
                limit = int(limit_text)
                if limit < 0:
                    raise ValueError("替换数量不能小于 0。")
                if limit == 0:
                    print("WARNING: replacing all clips rewrites many .blob files; the client may reject the copy during map loading. Use 5 first and test a disposable copy.", flush=True)
                elif limit > 0:
                    print("Small-batch profile: prefer Vo_NPC_Nao_ clips so the result is easy to test in town/NPC dialogue.", flush=True)
            else:
                output = ""
                limit = 5
            print(f"Replacement limit: {'all matching clips' if limit == 0 else str(limit) + ' clips maximum'}.", flush=True)
            command = [sys.executable, "-u", str(root / "transfer_jp_voice_resources.py"), "--tw-root", tw, "--max-clips", str(limit)]
            if limit > 0:
                command.extend(["--priority", r"Vo_NPC_Nao_"])
            if jp:
                command.extend(["--jp-root", jp])
            else:
                command.extend(["--cache-dir", str(root)])
            if args.mode == "transfer":
                command.extend(["--output-copy", output])
            else:
                command.extend(["--dry-run", "--report-dir", str(root / "scan-reports")])
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"mode: {args.mode}\npython: {sys.executable}\n")
            if args.mode != "dependencies":
                log.write(f"audio source: {'local-jp' if jp else 'bundled-cache'}\n")
            log.flush()
            print(f"Starting. Progress and errors are saved to {log_path}", flush=True)
            with subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
                assert process.stdout is not None
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                code = process.wait()
            log.write(f"\nexit code: {code}\n")
        print(f"{'Completed' if code == 0 else 'Failed'} (exit code {code}). Log: {log_path}", flush=True)
        return code
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.", flush=True)
        return 130
    except Exception as exc:
        message = f"ERROR: {exc}"
        print(message, flush=True)
        try:
            log_path.write_text(message + "\n", encoding="utf-8")
        except OSError:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
