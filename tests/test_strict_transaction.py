import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_DIR = (
    Path(__file__).resolve().parents[1]
    / "tools"
)
sys.path.insert(0, str(PACKAGE_DIR))

import transfer_jp_voice_resources as transfer  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CommitStagedBlobBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="voice-batch-test-")
        self.root = Path(self.temp_dir.name) / "output"
        self.package_dir = self.root / "appdata" / "AssetPackages"
        self.package_dir.mkdir(parents=True)
        self.originals: dict[Path, bytes] = {}
        self.staged: dict[Path, Path] = {}
        for name, before, after in (
            ("0001.blob", b"original-001", b"patched--001"),
            ("0002.blob", b"original-002", b"patched--002"),
        ):
            source = self.package_dir / name
            source.write_bytes(before)
            staged = self.root / "staged" / name
            staged.parent.mkdir(exist_ok=True)
            staged.write_bytes(after)
            self.originals[source] = before
            self.staged[source] = staged
        self.original_hashes = {
            source: hashlib.sha256(data).hexdigest()
            for source, data in self.originals.items()
        }
        self.manifest_path = self.root / "backup-manifest.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def assert_originals_restored(self) -> None:
        for source, data in self.originals.items():
            self.assertEqual(sha256(source), hashlib.sha256(data).hexdigest())
            self.assertEqual(source.stat().st_size, len(data))

    def read_manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def test_commits_multiple_blobs_and_records_manifest(self) -> None:
        backups = transfer.commit_staged_blob_batch(
            self.root, self.staged, self.original_hashes
        )

        self.assertEqual(len(backups), 2)
        for source, staged in self.staged.items():
            self.assertEqual(sha256(source), sha256(staged))
            self.assertEqual(source.stat().st_size, staged.stat().st_size)
        manifest = self.read_manifest()
        self.assertEqual(manifest["transaction_status"], "committed")
        self.assertEqual(len(manifest["files"]), 2)
        for entry in manifest["files"]:
            backup = self.root / entry["backup"]
            source = self.root / entry["path"]
            self.assertEqual(sha256(backup), entry["sha256_before"])
            self.assertEqual(backup.stat().st_size, entry["bytes"])
            self.assertEqual(source.stat().st_size, entry["bytes"])

    def test_source_hash_preflight_failure_does_not_write_any_blob(self) -> None:
        bad_hashes = dict(self.original_hashes)
        second = sorted(self.originals)[1]
        bad_hashes[second] = "0" * 64

        with self.assertRaisesRegex(RuntimeError, "changed during preparation"):
            transfer.commit_staged_blob_batch(self.root, self.staged, bad_hashes)

        self.assert_originals_restored()
        self.assertFalse(self.manifest_path.exists())
        self.assertFalse((self.root / ".MabinogiMobileVoiceMod_backup").exists())

    def test_second_replace_failure_rolls_back_every_committed_blob(self) -> None:
        second = sorted(self.originals)[1]
        original_replace = transfer.os.replace
        failed = False

        def fail_second_commit(source, destination):
            nonlocal failed
            if Path(destination) == second and not failed:
                failed = True
                raise OSError("injected second replace failure")
            return original_replace(source, destination)

        with mock.patch.object(transfer.os, "replace", side_effect=fail_second_commit):
            with self.assertRaisesRegex(RuntimeError, "rolled back"):
                transfer.commit_staged_blob_batch(
                    self.root, self.staged, self.original_hashes
                )

        self.assertTrue(failed)
        self.assert_originals_restored()
        self.assertEqual(self.read_manifest()["transaction_status"], "rolled_back")
if __name__ == "__main__":
    unittest.main()
