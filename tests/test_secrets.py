"""Run with stdlib unittest; uses only dummy keys in a workspace temp folder."""

from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR.parent))
from gemeow_secrets import SecretStore  # noqa: E402


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="secrets-test-", dir=WORK_DIR)
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "new-data-dir"
        self.store = SecretStore(self.data_dir)

    def test_empty_store_does_not_create_files(self):
        self.assertEqual(self.store.load(), "")
        self.assertFalse(self.data_dir.exists())
        self.store.clear()

    def test_available_reports_platform(self):
        self.assertEqual(self.store.available, os.name == "nt")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_save_load_replace_and_clear(self):
        first = "dummy-test-key-가짜-1234567890"
        self.store.save(first)
        saved = self.data_dir / "api_key.dpapi"
        self.assertNotIn(first.encode("utf-8"), saved.read_bytes())
        self.assertEqual(SecretStore(self.data_dir).load(), first)
        second = "dummy-replacement-key-0987654321"
        self.store.save(second)
        self.assertEqual(self.store.load(), second)
        self.assertNotIn(second.encode("utf-8"), saved.read_bytes())
        self.assertEqual([entry.name for entry in self.data_dir.iterdir()], ["api_key.dpapi"])
        self.store.clear()
        self.assertEqual(self.store.load(), "")
        self.store.clear()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_tampered_ciphertext_is_rejected_and_preserved(self):
        self.store.save("dummy-corruption-test-key")
        saved = self.data_dir / "api_key.dpapi"
        damaged = bytearray(saved.read_bytes())
        damaged[-1] ^= 0xFF
        saved.write_bytes(damaged)
        with self.assertRaisesRegex(RuntimeError, "복호화하지 못했습니다"):
            self.store.load()
        self.assertEqual(saved.read_bytes(), bytes(damaged))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_invalid_header_is_rejected_and_preserved(self):
        self.data_dir.mkdir()
        saved = self.data_dir / "api_key.dpapi"
        damaged = b"invalid-not-a-saved-key"
        saved.write_bytes(damaged)
        with self.assertRaisesRegex(RuntimeError, "손상되었습니다"):
            self.store.load()
        self.assertEqual(saved.read_bytes(), damaged)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_failed_replace_keeps_previous_key(self):
        self.store.save("dummy-original-key")
        with patch("gemeow_secrets.os.replace", side_effect=PermissionError("test failure")):
            with self.assertRaisesRegex(RuntimeError, "저장하지 못했습니다"):
                self.store.save("dummy-new-key")
        self.assertEqual(self.store.load(), "dummy-original-key")
        self.assertEqual([entry.name for entry in self.data_dir.iterdir()], ["api_key.dpapi"])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_empty_key_is_rejected_without_creating_file(self):
        with self.assertRaises(ValueError):
            self.store.save("   ")
        self.assertFalse(self.data_dir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
