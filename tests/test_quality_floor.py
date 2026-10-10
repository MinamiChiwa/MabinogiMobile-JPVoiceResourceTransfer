import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "tools"
import sys
sys.path.insert(0, str(PACKAGE_DIR))
import transfer_jp_voice_resources as transfer  # noqa: E402


class QualityFloorTests(unittest.TestCase):
    def test_ima_candidate_below_8000hz_is_rejected(self):
        class OversizedFsbank:
            def encode_pcm(self, *args, **kwargs):
                return b"x" * 101

        class TinyIma:
            def encoded_size(self, frames):
                return frames

        pcm = b"\0" * (2 * 44100)
        source_info = {"channels": 1, "frequency": 44100}
        with tempfile.TemporaryDirectory(prefix="quality-floor-") as temp:
            with mock.patch.object(transfer, "parse_fsb5", return_value=source_info), \
                 mock.patch.object(transfer, "decode_fsb5_pcm", return_value=(pcm, 1, 44100)), \
                 mock.patch.object(transfer, "trim_zero_edges", return_value=(pcm, {"retained_duration": 1.0})), \
                 mock.patch.object(transfer, "load_fsbank_bridge", return_value=OversizedFsbank()), \
                 mock.patch.object(transfer, "find_fsbank_dir", return_value=Path(temp)), \
                 mock.patch.object(transfer, "load_ima_encoder", return_value=TinyIma()), \
                 mock.patch.object(transfer, "fsbank_fingerprint", return_value=("test",)), \
                 mock.patch.object(transfer, "resample_pcm", return_value=pcm):
                result = transfer.compress_fsb5_to_capacity(
                    b"source", 100, preferred_channels=1, preferred_frequency=44100,
                    fsbank_dir=Path(temp), fsbank_cache_dir=Path(temp),
                )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
