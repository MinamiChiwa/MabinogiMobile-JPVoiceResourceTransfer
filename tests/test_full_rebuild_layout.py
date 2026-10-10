import importlib.util
import struct
import sys
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(PACKAGE))
spec = importlib.util.spec_from_file_location("full_rebuild_transfer", PACKAGE / "transfer_jp_voice_resources.py")
transfer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = transfer
spec.loader.exec_module(transfer)


def unityfs_stub(payload: bytes) -> bytes:
    prefix = b"UnityFS\0" + b"\0\0\0\0" + b"2021.3\0" + b"51f1\0"
    size = len(prefix) + 8 + len(payload)
    return prefix + struct.pack(">Q", size) + payload


class FullRebuildLayoutTests(unittest.TestCase):
    def test_blob_end_table_follows_grown_bundle_without_changing_ordinal(self):
        first = unityfs_stub(b"first")
        second = unityfs_stub(b"second-grown" * 3)
        header_size = 8 + 2 * 4
        original = bytearray(b"\xba\xc3\xa9\xe5" + struct.pack("<I", header_size))
        original.extend(struct.pack("<II", header_size + len(first), header_size + len(first) + len(unityfs_stub(b"second"))))
        original.extend(first)
        original.extend(second)

        rebuilt = transfer.rewrite_blob_end_table(bytes(original))
        self.assertEqual(rebuilt[:4], b"\xba\xc3\xa9\xe5")
        ends = struct.unpack_from("<II", rebuilt, 8)
        slices = list(transfer.iter_unityfs_slices(rebuilt))
        self.assertEqual(len(slices), 2)
        self.assertEqual(ends, (slices[0][0] + len(slices[0][1]), slices[1][0] + len(slices[1][1])))
        self.assertEqual(ends[-1], len(rebuilt))


if __name__ == "__main__":
    unittest.main()
