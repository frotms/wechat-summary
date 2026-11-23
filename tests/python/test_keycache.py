# -*- coding: utf-8 -*-
"""Tests for the 4.2 raw-key cache."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from lib.keycache import SCHEMA, db_rels, load_keys, save_keys  # noqa: E402


class KeyCacheTest(unittest.TestCase):
    def make_root(self, root: Path):
        db = root / "db_storage" / "message" / "message_0.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"\x00" * 8192)
        tiny = root / "db_storage" / "tiny.db"
        tiny.write_bytes(b"\x00" * 10)
        return db.parent.parent

    def test_db_rels_skips_small_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = self.make_root(root)
            self.assertEqual(db_rels(data_root), ["message/message_0.db"])

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = self.make_root(root)
            account = root / "account"
            account.mkdir()
            keys = {"message/message_0.db": {"enc_key": "ab" * 32, "salt": "cd" * 16}}
            path = root / "cache" / "keys.json"
            save_keys(path, data_root, account, keys)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], SCHEMA)
            self.assertEqual(load_keys(path, db_rels(data_root)), keys)

    def test_stale_or_invalid_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = self.make_root(root)
            account = root / "account"
            account.mkdir()
            keys = {"message/message_0.db": {"enc_key": "ab" * 32, "salt": "cd" * 16}}
            path = root / "keys.json"
            save_keys(path, data_root, account, keys)
            self.assertIsNone(load_keys(path, ["message/message_0.db", "message/new.db"]))

            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_keys(path))

            path.write_text(json.dumps({"schema": "other", "keys": keys}), encoding="utf-8")
            self.assertIsNone(load_keys(path))


if __name__ == "__main__":
    unittest.main()
