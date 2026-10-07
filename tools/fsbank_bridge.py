#!/usr/bin/env python3
"""Small bridge for encoding one PCM stream with the bundled FSBank DLL.

The bridge deliberately creates one temporary WAV and one FSB per candidate.
FSBank then writes the complete FSB5 Vorbis sample, including the setup data
and metadata selected by the encoder.  It does not modify a client directory.
"""

from __future__ import annotations

import importlib.util
import tempfile
import wave
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_wrapper():
    wrapper_path = Path(__file__).with_name("fsbank_encode.py")
    spec = importlib.util.spec_from_file_location("mabinogi_fsbank_encode", wrapper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load FSBank wrapper: {wrapper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_pcm16_wav(path: Path, pcm: bytes, channels: int, frequency: int) -> None:
    if channels not in (1, 2):
        raise ValueError(f"unsupported channel count: {channels}")
    if frequency <= 0 or len(pcm) % (2 * channels):
        raise ValueError("PCM does not contain complete 16-bit frames")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frequency)
        wav_file.writeframes(pcm)


def encode_pcm(
    pcm: bytes,
    channels: int,
    frequency: int,
    quality: int,
    library_dir: Path,
    cache_dir: Path,
    target_sample_rate: int | None = None,
) -> bytes:
    """Return one complete FSBank FSB5 sample for signed PCM16 input."""

    wrapper = _load_wrapper()
    library_dir = library_dir.resolve()
    cache_dir = cache_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="mabinogi-fsbank-") as temp_dir:
        temp = Path(temp_dir)
        input_wav = temp / "input.wav"
        output_fsb = temp / "output.fsb"
        write_pcm16_wav(input_wav, pcm, channels, frequency)
        wrapper.encode(
            input_wav,
            output_fsb,
            int(quality),
            cache_dir,
            1,
            library_dir,
            float(target_sample_rate or 0),
        )
        return output_fsb.read_bytes()
