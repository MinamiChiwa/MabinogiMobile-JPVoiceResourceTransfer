"""Build the Windows x64 onedir release from canonical tools/ sources.

Create a CPython 3.12 x64 venv and install requirements-build.lock first.
The resulting release never installs dependencies or reads the network.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

TOOLS = Path(__file__).resolve().parent
SOURCES = ("resource_transfer_launcher.py", "transfer_jp_voice_resources.py",
           "resource_voice_index.py", "resource_progress.py", "portable_runtime.py", "ima_fsb_encoder.py",
           "audit_voice_coverage.py", "output_paths.py")


def locked_versions() -> dict[str, str]:
    versions = {}
    for name in ("requirements-runtime.lock", "requirements-build.lock"):
        for line in (TOOLS / name).read_text(encoding="utf-8").splitlines():
            if "==" in line and not line.startswith("#"):
                package, expected = line.strip().split("==", 1)
                actual = metadata.version(package)
                if actual != expected:
                    raise RuntimeError(f"{package}: expected {expected}, found {actual}; use requirements-build.lock")
                versions[package] = actual
    return versions


def copy_licenses(destination: Path, versions: dict[str, str]) -> None:
    license_root = destination / "licenses"
    license_root.mkdir(exist_ok=True)
    for name in versions:
        distribution = metadata.distribution(name)
        for record in distribution.files or []:
            if not any(token in str(record).lower() for token in ("license", "copying", "notice")):
                continue
            source = distribution.locate_file(record)
            if source.is_file():
                target = license_root / name / Path(str(record))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (license_root / f"{name}-metadata.txt").write_text(distribution.read_text("METADATA") or "", encoding="utf-8")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, license_root / "Python-Tcl-Tk-LICENSE.txt")
    notices = ["Portable runtime components and their supplied license texts:",
               f"CPython {platform.python_version()} / Tcl / Tk: licenses/Python-Tcl-Tk-LICENSE.txt"]
    notices.extend(f"{name} {version}: licenses/{name}/ and licenses/{name}-metadata.txt"
                   for name, version in versions.items())
    notices.extend(["", "FMOD native runtime is supplied by fmod_toolkit; Firelight Technologies Pty Ltd.",
                    "The Python wrapper MIT license does not replace FMOD native runtime terms.",
                    "FMOD terms: https://www.fmod.com/legal", "No client voice resources are included."])
    (destination / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices) + "\n", encoding="utf-8")


def build(output: Path) -> Path:
    if sys.platform != "win32" or platform.architecture()[0] != "64bit" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Build with CPython 3.12 x64 on Windows")
    versions = locked_versions()
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noupx",
               "--name", "JPVoiceTransfer", "--exclude-module", "numpy", "--paths", str(TOOLS),
               "--distpath", str(output / "dist"), "--workpath", str(output / "build"),
               "--specpath", str(output)]
    for name in ("UnityPy", "fmod_toolkit", "archspec", "astc_encoder"):
        command.extend(["--collect-all", name])
    command.append(str(TOOLS / "resource_transfer_launcher.py"))
    subprocess.run(command, check=True)
    release = output / "dist" / "JPVoiceTransfer"
    for name in ("_run_tool.bat", "run_transfer.bat", "run_scan.bat", "install_dependencies.bat",
                 "requirements-runtime.lock"):
        shutil.copy2(TOOLS / name, release / name)
    # Rebuilding the same output replaces the release; license files are metadata only.
    copy_licenses(release, versions)
    source_hashes = {name: hashlib.sha256((TOOLS / name).read_bytes()).hexdigest() for name in SOURCES
                     if (TOOLS / name).is_file()}
    (release / "BUILDINFO.json").write_text(json.dumps({"version": "0.1.6.2", "python": platform.python_version(),
          "platform": "Windows-x64", "dependencies": versions, "source_sha256": source_hashes},
          ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    executable = release / "JPVoiceTransfer.exe"
    subprocess.run([str(executable), "--self-check"], check=True)
    subprocess.run([str(executable), "--worker", "--help"], check=True, stdout=subprocess.DEVNULL)
    checksums = [hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(release).as_posix()
                 for path in sorted(release.rglob("*")) if path.is_file() and path.name != "CHECKSUMS.txt"]
    (release / "CHECKSUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return release


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Build directory outside source and client trees")
    args = parser.parse_args()
    print(build(args.output.expanduser().resolve()))
