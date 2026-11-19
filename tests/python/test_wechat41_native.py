# -*- coding: utf-8 -*-
"""Tests for the 4.1 native path: SQLCipher -> work/ -> native export."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECRYPT = ROOT / "scripts" / "wechat41-decrypt.py"
NATIVE = ROOT / "scripts" / "native-backend.py"
TARGET = "123@chatroom"
TS = 1767578400

try:
    import sqlcipher3
    HAVE_SQLCIPHER = True
except Exception:
    HAVE_SQLCIPHER = False


@unittest.skipUnless(HAVE_SQLCIPHER, "sqlcipher3 not installed")
class WeChat41NativeTest(unittest.TestCase):
    def make_encrypted(self, root: Path):
        account = root / "account_1234"
        db_storage = account / "db_storage"
        for sub in ("session", "contact", "message"):
            (db_storage / sub).mkdir(parents=True)
        passphrase = "testpass"
        passphrase_hex = passphrase.encode().hex()

        def encrypted(path, schema):
            conn = sqlcipher3.connect(str(path))
            try:
                conn.execute("PRAGMA key = 'testpass'")
                conn.executescript(schema)
                conn.commit()
            finally:
                conn.close()

        encrypted(db_storage / "session" / "session.db",
                  "create table SessionTable (username text, last_timestamp integer, summary text);"
                  f"insert into SessionTable values ('{TARGET}', {TS}, 'hello');")
        encrypted(db_storage / "contact" / "contact.db",
                  "create table contact (username text, remark text, nick_name text);"
                  "insert into contact values ('wxid_me', '我', 'Me');"
                  "create table my_user_info (user_name text, nick_name text);"
                  "insert into my_user_info values ('wxid_me', 'Me');")
        table = "Msg_" + hashlib.md5(TARGET.encode()).hexdigest()
        encrypted(db_storage / "message" / "message_0.db",
                  "create table Name2Id (user_name text);"
                  "insert into Name2Id(rowid, user_name) values (1, 'wxid_lisi');"
                  f'create table "{table}" (local_id integer, server_id text, local_type integer, '
                  "real_sender_id integer, create_time integer, message_content blob, packed_info_data blob);"
                  f'insert into "{table}" values (1, "srv-1", 1, 1, {TS}, "hello", "");')

        cfg = root / "config.json"
        cfg.write_text(json.dumps({
            "dbPath": str(account),
            "wxid": "account_1234",
            "decryptKey": passphrase_hex,
        }), encoding="utf-8")
        return account, cfg

    def test_decrypt_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account, cfg = self.make_encrypted(root)
            work = root / "work"
            res = subprocess.run([
                sys.executable, str(DECRYPT), "--config", str(cfg),
                "--work-dir", str(work), "--json",
            ], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(res.returncode, 0, res.stderr)
            info = json.loads(res.stdout)
            self.assertTrue(info["success"])
            self.assertEqual(info["accountRoot"], str(account.resolve()))
            self.assertTrue((work / "message" / "message_0.db").is_file())

            out = root / "out"
            env = {
                **os.environ,
                "WEFLOW_NATIVE_CONFIG": str(cfg),
                "WEFLOW_NATIVE_WORK": str(work),
                "WEFLOW_NATIVE_ACCOUNT": str(account),
                "WEFLOW_NATIVE_PYTHON": sys.executable,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
            res = subprocess.run([
                sys.executable, str(NATIVE), "export", TARGET, "json",
                "--from", "2026-01-05", "--to", "2026-01-05",
                "--output", str(out), "--limit", "0", "--json",
            ], capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads((out / f"{TARGET}_messages.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["messages"][0]["rawContent"], "hello")
            self.assertEqual(payload["messages"][0]["serverId"], "srv-1")


if __name__ == "__main__":
    unittest.main()
