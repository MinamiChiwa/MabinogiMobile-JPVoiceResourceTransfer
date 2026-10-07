"""Preserve real PCM content when only exact-zero edge cuts are authorized."""

import hashlib
import math
from pathlib import Path
import struct
import sys
import unittest


PACKAGE = Path(__file__).resolve().parents[1] / "release" / "MabinogiMobile_JPVoiceResourceTransfer_0.1.5"
sys.path.insert(0, str(PACKAGE))
import transfer_jp_voice_resources as transfer  # noqa: E402


def pcm16(values):
    return struct.pack("<" + "h" * len(values), *values)


def values(pcm):
    return struct.unpack("<" + "h" * (len(pcm) // 2), pcm)


class ExactZeroEdgeTests(unittest.TestCase):
    def test_one_unit_weak_sound_and_internal_zeros_are_kept(self):
        # 1/-1 amplitudes are real PCM, despite being far below audibility.
        sound = [1, 0, 0, -1]
        source = pcm16([0] * 30 + sound + [0] * 30)
        trimmed, details = transfer.trim_zero_edges(source, 1, 1000)
        self.assertEqual(values(trimmed), (0,) * 10 + tuple(sound) + (0,) * 10)
        self.assertEqual(details["leading_frames"], 20)
        self.assertEqual(details["trailing_frames"], 20)
        self.assertEqual(details["silence_threshold_pcm"], 0)
        self.assertEqual(details["retained_frames"], 24)

    def test_stereo_frame_with_only_one_active_channel_is_not_silence(self):
        # Neither channel may be inspected alone when finding edge silence.
        active = [(0, 1), (0, 0), (-1, 0)]
        frames = [(0, 0)] * 20 + active + [(0, 0)] * 20
        source = pcm16([sample for frame in frames for sample in frame])
        trimmed, details = transfer.trim_zero_edges(source, 2, 1000)
        expected = [(0, 0)] * 10 + active + [(0, 0)] * 10
        self.assertEqual(values(trimmed), tuple(sample for frame in expected for sample in frame))
        self.assertEqual(details["retained_frames"], 23)
        self.assertEqual(len(trimmed), 23 * 4)

    def test_all_zero_clip_remains_nonempty_and_unchanged(self):
        for channels in (1, 2):
            with self.subTest(channels=channels):
                source = bytes(123 * 2 * channels)
                trimmed, details = transfer.trim_zero_edges(source, channels, 22050)
                self.assertEqual(trimmed, source)
                self.assertEqual(details["retained_frames"], 123)
                self.assertEqual(details["leading_frames"], 0)
                self.assertEqual(details["trailing_frames"], 0)

    def test_integer_cuts_reconstruct_source_and_keep_ten_ms_safety(self):
        # 11025 Hz does not express ten milliseconds as an integer frame.
        # The retained safety must round upward, never remove part of a frame.
        rate = 11025
        padding = math.ceil(rate / 100)
        sound = [1, -1, 32767, -32768, 0, 17, -19]
        source = pcm16([0] * 300 + sound + [0] * 400)
        trimmed, details = transfer.trim_zero_edges(source, 1, rate)
        self.assertEqual(details["leading_frames"], 300 - padding)
        self.assertEqual(details["trailing_frames"], 400 - padding)
        self.assertEqual(details["retained_frames"], len(sound) + 2 * padding)
        self.assertGreaterEqual(padding / rate, 0.010)
        reconstructed = (
            bytes(details["leading_frames"] * 2)
            + trimmed
            + bytes(details["trailing_frames"] * 2)
        )
        self.assertEqual(reconstructed, source)
        self.assertEqual(hashlib.sha256(reconstructed).digest(), hashlib.sha256(source).digest())
        self.assertAlmostEqual(
            details["original_duration"],
            details["leading_seconds"] + details["retained_duration"] + details["trailing_seconds"],
        )

    def test_sound_at_boundaries_does_not_require_padding_outside_clip(self):
        source = pcm16([1, 0, -1])
        trimmed, details = transfer.trim_zero_edges(source, 1, 48000)
        self.assertEqual(trimmed, source)
        self.assertEqual(details["leading_frames"], 0)
        self.assertEqual(details["trailing_frames"], 0)

    def test_empty_pcm_is_rejected_instead_of_producing_zero_duration(self):
        with self.assertRaises(ValueError):
            transfer.trim_zero_edges(b"", 1, 22050)

    def test_invalid_layout_is_rejected(self):
        cases = ((b"\0", 1, 22050), (bytes(6), 2, 22050), (bytes(2), 0, 22050),
                 (bytes(2), -1, 22050), (bytes(2), 1, 0), (bytes(2), 1, -1))
        for source, channels, rate in cases:
            with self.subTest(source=source, channels=channels, rate=rate):
                with self.assertRaises(ValueError):
                    transfer.trim_zero_edges(source, channels, rate)


class ResampledGainTests(unittest.TestCase):
    def test_already_louder_audio_is_not_attenuated_or_amplified(self):
        source, converted = pcm16([1000, -1000]), pcm16([1500, -1500])
        restored, gain = transfer.restore_resampled_gain(source, converted)
        self.assertEqual(gain, 1.0)
        self.assertEqual(restored, converted)

    def test_recovery_has_six_db_gain_cap(self):
        source, converted = pcm16([12000, -12000]), pcm16([100, -100])
        restored, gain = transfer.restore_resampled_gain(source, converted)
        self.assertEqual(gain, 2.0)
        self.assertEqual(values(restored), (200, -200))

    def test_peak_limit_prevents_new_clipping_and_keeps_sample_count(self):
        source = pcm16([30000, -30000] * 10)
        converted = pcm16([20000, -20000] + [0] * 18)
        restored, gain = transfer.restore_resampled_gain(source, converted)
        self.assertGreater(gain, 1.0)
        self.assertLessEqual(gain, 2.0)
        self.assertEqual(len(restored), len(converted))
        self.assertLessEqual(max(abs(sample) for sample in values(restored)), 31129)
        self.assertEqual(values(restored)[:2], (31129, -31129))
        self.assertEqual(values(restored)[2:], (0,) * 18)

    def test_existing_full_scale_samples_are_not_newly_clipped(self):
        source = pcm16([32767, -32768] * 4)
        converted = pcm16([32767, -32768] + [0] * 6)
        restored, gain = transfer.restore_resampled_gain(source, converted)
        self.assertEqual(gain, 1.0)
        self.assertEqual(restored, converted)

    def test_all_zero_or_zero_source_never_creates_artificial_sound(self):
        for source, converted in ((pcm16([100, -100]), bytes(4)), (bytes(4), bytes(4)),
                                  (bytes(4), pcm16([100, -100]))):
            with self.subTest(source=source, converted=converted):
                restored, gain = transfer.restore_resampled_gain(source, converted)
                self.assertEqual(gain, 1.0)
                self.assertEqual(restored, converted)

    def test_empty_and_partial_samples_are_rejected(self):
        valid = pcm16([1])
        for source, converted in ((b"", valid), (valid, b""), (b"\0", valid), (valid, b"\0")):
            with self.subTest(source=source, converted=converted):
                with self.assertRaises(ValueError):
                    transfer.restore_resampled_gain(source, converted)


if __name__ == "__main__":
    unittest.main()
