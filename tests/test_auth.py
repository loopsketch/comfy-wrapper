"""キーストア。平文を保存しないこと、失効が効くことを見る。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _bootstrap  # noqa: F401

import auth


class KeyStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "keys.json"

    def test_issue_and_verify(self):
        store = auth.KeyStore(self.path)
        key, record = store.issue("comfy")
        self.assertTrue(key.startswith("cw_"))
        self.assertIsNotNone(store.verify(key))
        self.assertEqual(store.verify(key).id, record.id)

    def test_plaintext_is_never_stored(self):
        """**リモートに渡るのはハッシュだけ。** 平文が残っていたら意味が無い。"""
        store = auth.KeyStore(self.path)
        key, _ = store.issue("comfy")
        saved = self.path.read_text()
        self.assertNotIn(key, saved)
        self.assertIn(auth.hash_key(key), saved)

    def test_file_is_owner_only(self):
        store = auth.KeyStore(self.path)
        store.issue("comfy")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_wrong_key_is_rejected(self):
        store = auth.KeyStore(self.path)
        store.issue("comfy")
        self.assertIsNone(store.verify("cw_wrong"))

    def test_revoke(self):
        store = auth.KeyStore(self.path)
        key, record = store.issue("comfy")
        self.assertTrue(store.revoke(record.id))
        self.assertIsNone(store.verify(key))
        self.assertFalse(store.revoke("no-such-id"))

    def test_reload_keeps_keys(self):
        """セッションを立て直しても同じキーで通ること。"""
        key, _ = auth.KeyStore(self.path).issue("comfy")
        self.assertIsNotNone(auth.KeyStore(self.path).verify(key))

    def test_missing_file_is_empty(self):
        store = auth.KeyStore(Path(self.tmp.name) / "none.json")
        self.assertEqual(store.records, [])
        self.assertIsNone(store.verify("cw_anything"))

    def test_keys_are_unique(self):
        store = auth.KeyStore(self.path)
        keys = {store.issue(f"k{i}")[0] for i in range(5)}
        self.assertEqual(len(keys), 5)
        self.assertEqual(len(json.loads(self.path.read_text())["keys"]), 5)


if __name__ == "__main__":
    unittest.main()
