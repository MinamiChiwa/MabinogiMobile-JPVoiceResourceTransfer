#!/usr/bin/env python3
"""Small, deterministic wrapper around the bundled FMOD FSBank DLL.

The game uses FMOD FSB5 Vorbis resources.  This wrapper intentionally asks
FSBank to write a real FSB5 instead of constructing a bank from ordinary Ogg
packets.  It is kept as a separate tool so the encoder can be tested without
touching a client directory.
"""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
from contextlib import contextmanager


class FSBANK_SUBSOUND(ctypes.Structure):
    _fields_ = [
        ("fileNames", ctypes.POINTER(ctypes.c_char_p)),
        ("fileData", ctypes.POINTER(ctypes.c_void_p)),
        ("fileDataLengths", ctypes.POINTER(ctypes.c_uint)),
        ("numFiles", ctypes.c_uint),
        ("overrideFlags", ctypes.c_uint),
        ("overrideQuality", ctypes.c_uint),
        ("desiredSampleRate", ctypes.c_float),
        ("percentOptimizedRate", ctypes.c_float),
    ]


RESULT_NAMES = {
    0: "FSBANK_OK",
    1: "FSBANK_ERR_CACHE_CHUNKNOTFOUND",
    2: "FSBANK_ERR_CANCELLED",
    3: "FSBANK_ERR_CANNOT_CONTINUE",
    4: "FSBANK_ERR_ENCODER",
    5: "FSBANK_ERR_ENCODER_INIT",
    6: "FSBANK_ERR_ENCODER_NOTSUPPORTED",
    7: "FSBANK_ERR_FILE_OS",
    8: "FSBANK_ERR_FILE_NOTFOUND",
    9: "FSBANK_ERR_FMOD",
    10: "FSBANK_ERR_INITIALIZED",
    11: "FSBANK_ERR_INVALID_FORMAT",
    12: "FSBANK_ERR_INVALID_PARAM",
    13: "FSBANK_ERR_MEMORY",
    14: "FSBANK_ERR_UNINITIALIZED",
    15: "FSBANK_ERR_WRITER_FORMAT",
    16: "FSBANK_WARN_CANNOTLOOP",
    17: "FSBANK_WARN_IGNORED_FILTERHIGHFREQ",
    18: "FSBANK_WARN_IGNORED_DISABLESEEKING",
    19: "FSBANK_WARN_FORCED_DONTWRITENAMES",
    20: "FSBANK_ERR_ENCODER_FILE_NOTFOUND",
    21: "FSBANK_ERR_ENCODER_FILE_BAD",
}


def result_name(code: int) -> str:
    return RESULT_NAMES.get(int(code), f"FSBANK_RESULT_{code}")


def load_library(directory: Path):
    directory = directory.resolve()
    # fsbank.dll loads fmod.dll and libfsbvorbis64.dll from its own folder.
    # Keep the directory handle alive for the duration of the process.
    add_dir = getattr(os, "add_dll_directory", None)
    dll_dir = add_dir(str(directory)) if add_dir else None
    library = ctypes.WinDLL(str(directory / "fsbank.dll"))
    return library, dll_dir


@contextmanager
def encoder_working_directory(directory: Path):
    """FSBank locates its codec plug-ins relative to the process cwd."""
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


def encode(input_wav: Path, output_fsb: Path, quality: int, cache_dir: Path,
           threads: int, library_dir: Path, sample_rate: float = 0.0) -> int:
    # Resolve every caller-supplied path before changing cwd.  FSBank loads
    # codec plug-ins relative to its DLL directory, but the input/output and
    # cache paths must remain relative to the caller's working directory.
    input_wav = input_wav.resolve()
    output_fsb = output_fsb.resolve()
    cache_dir = cache_dir.resolve()
    library_dir = library_dir.resolve()
    if not input_wav.is_file():
        raise FileNotFoundError(input_wav)
    if not 0 <= quality <= 100:
        raise ValueError("quality must be between 0 and 100")
    output_fsb.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_fsb.unlink(missing_ok=True)
    library, dll_dir = load_library(library_dir)
    try:
        with encoder_working_directory(library_dir):
            init = library.FSBank_Init
            init.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p]
            init.restype = ctypes.c_int
            release = library.FSBank_Release
            release.argtypes = []
            release.restype = ctypes.c_int
            build = library.FSBank_Build
            build.argtypes = [
                ctypes.POINTER(FSBANK_SUBSOUND), ctypes.c_uint, ctypes.c_int,
                ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p, ctypes.c_char_p,
            ]
            build.restype = ctypes.c_int

            cache_bytes = os.fsencode(str(cache_dir.resolve()))
            code = int(init(0, 0, max(1, int(threads)), cache_bytes))
            if code != 0:
                raise RuntimeError(f"FSBank_Init failed: {result_name(code)} ({code})")
            try:
                name = os.fsencode(str(input_wav.resolve()))
                names = (ctypes.c_char_p * 1)(name)
                subsound = FSBANK_SUBSOUND()
                subsound.fileNames = names
                subsound.numFiles = 1
                subsound.overrideFlags = 0x00000003
                if sample_rate > 0:
                    subsound.overrideFlags |= 0x00000010  # FSBANK_BUILD_OPTIMIZESAMPLERATE
                subsound.overrideQuality = int(quality)
                subsound.desiredSampleRate = float(sample_rate)
                subsound.percentOptimizedRate = 0.0
                output_bytes = os.fsencode(str(output_fsb.resolve()))
                build_flags = 0x00000083  # no loops, sync points or sample names
                if sample_rate > 0:
                    build_flags |= 0x00000010
                code = int(build(ctypes.byref(subsound), 1, 3, build_flags,
                                 int(quality), None, output_bytes))
                if code not in (0, 16):
                    raise RuntimeError(f"FSBank_Build failed: {result_name(code)} ({code})")
            finally:
                release()
    finally:
        if dll_dir is not None:
            dll_dir.close()
    if not output_fsb.is_file() or output_fsb.stat().st_size < 32:
        raise RuntimeError(f"FSBank did not create a valid output: {output_fsb}")
    return output_fsb.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quality", type=int, default=0)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--library-dir", type=Path, required=True)
    parser.add_argument("--sample-rate", type=float, default=0.0,
                        help="Optional target sample rate (for example 8000)")
    args = parser.parse_args()
    size = encode(args.input, args.output, args.quality, args.cache,
                  args.threads, args.library_dir, args.sample_rate)
    print(f"{args.output}\t{size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
