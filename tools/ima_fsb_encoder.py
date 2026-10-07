"""Encode mono signed PCM16 as a real single-sample FSB5 IMA ADPCM bank.

The audio uses the Xbox/FSB IMA form: 36-byte blocks, a PCM predictor and step
index in each block header, low nibble first, and 64 output frames per block.
The final high nibble is unused. The logical FSB frame count excludes padding.

This is codec/container support, not proof that any particular game loads a
modified resource. See diagnostic/adpcm-smallslot-audit/README.md for offline
validation and public format references.
"""
from __future__ import annotations

import struct

_STEP_TABLE = (
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,
    60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,
    337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,
    1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,
    5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,
    16818,18500,20350,22385,24623,27086,29794,32767,
)
_INDEX_DELTA = (-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8)
_BLOCK_BYTES = 36
_BLOCK_FRAMES = 64
_BASE_HEADER_BYTES = 60
_SAMPLE_HEADER_BYTES = 36
_MAX_FRAMES = (1 << 30) - 1


def _check_channels(channels: int) -> None:
    if channels != 1:
        raise ValueError("FSB IMA encoder currently supports mono PCM only")


def encoded_size(frames: int, channels: int = 1) -> int:
    """Return the exact bank size, including its header and data alignment.

    Zero frames is allowed for capacity calculations; encode_pcm rejects an
    empty recording. Sample count must fit FSB5's 30-bit logical count field.
    """
    _check_channels(channels)
    if not isinstance(frames, int) or isinstance(frames, bool) or not 0 <= frames <= _MAX_FRAMES:
        raise ValueError("frames must be a nonnegative 30-bit integer")
    blocks = (frames + _BLOCK_FRAMES - 1) // _BLOCK_FRAMES
    data_size = (blocks * _BLOCK_BYTES + 31) // 32 * 32
    return _BASE_HEADER_BYTES + _SAMPLE_HEADER_BYTES + data_size


def _encode_block(samples: list[int], initial_index: int, real_frames: int) -> tuple[int, bytes]:
    predictor = samples[0]
    index = initial_index
    codes: list[int] = []
    squared_error = 0
    for frame, target in enumerate(samples[1:], start=1):
        step = _STEP_TABLE[index]
        best_error = 1 << 32
        best_code = 0
        best_value = predictor
        for code in range(16):
            delta = step >> 3
            if code & 1:
                delta += step >> 2
            if code & 2:
                delta += step >> 1
            if code & 4:
                delta += step
            value = predictor + (-delta if code & 8 else delta)
            value = max(-32768, min(32767, value))
            error = abs(target - value)
            if error < best_error:
                best_error, best_code, best_value = error, code, value
        predictor = best_value
        if frame < real_frames:
            squared_error += best_error * best_error
        index = max(0, min(88, index + _INDEX_DELTA[best_code]))
        codes.append(best_code)
    codes.append(0)  # Xbox IMA ignores the last nibble in the block.
    packed = bytes(codes[i] | codes[i + 1] << 4 for i in range(0, 64, 2))
    return squared_error, struct.pack("<hBB", samples[0], initial_index, 0) + packed


def encode_pcm(pcm: bytes, channels: int, frequency: int) -> bytes:
    """Encode existing mono PCM16 without changing frames, gain or frequency.

    Every input frame is represented. Each final block is physically completed
    by holding the last sample; those extra frames are excluded from the FSB
    logical frame count. No real input frame is trimmed and no tempo changes.
    """
    _check_channels(channels)
    if not isinstance(frequency, int) or isinstance(frequency, bool) or not 1 <= frequency <= 0xFFFFFFFF:
        raise ValueError("frequency must be a positive uint32 sample rate")
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
        raise ValueError("pcm must contain nonempty complete signed PCM16 frames")
    frames = len(pcm) // 2
    total_size = encoded_size(frames, channels)
    samples = [value[0] for value in struct.iter_unpack("<h", pcm)]
    data = bytearray()
    for offset in range(0, frames, _BLOCK_FRAMES):
        block = samples[offset:offset + _BLOCK_FRAMES]
        # Do not let unplayed end padding affect the initial-index quality
        # decision. An input block is always encoded with all real frames.
        real_frames = len(block)
        padded = block + [block[-1]] * (_BLOCK_FRAMES - real_frames)
        best: tuple[int, bytes] | None = None
        for initial_index in range(89):
            candidate = _encode_block(padded, initial_index, real_frames)
            if best is None or candidate[0] < best[0]:
                best = candidate
        assert best is not None
        data.extend(best[1])
    data.extend(b"\0" * (total_size - 96 - len(data)))

    # Native FSBank version-1 envelope: single subsound, mode 7, no names,
    # reserved/optional identifier fields zero. Data begins at 96 bytes.
    header = struct.pack("<4s6I", b"FSB5", 1, 1, 36, 0, len(data), 7) + b"\0" * 32
    # Always use FREQUENCY extra flag type 2, so arbitrary real playback rates
    # are unambiguous. Chunk size is 4, with no following metadata chunk.
    packed_sample = (frames << 34) | 1
    frequency_chunk = struct.pack("<II", (2 << 25) | (4 << 1), frequency)
    sample_header = struct.pack("<Q", packed_sample) + frequency_chunk + b"\0" * 20
    return header + sample_header + bytes(data)
