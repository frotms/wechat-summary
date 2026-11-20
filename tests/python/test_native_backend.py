# -*- coding: utf-8 -*-
"""Tests for the Level 2 native 4.2 backend (no weflow-cli)."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "native-backend.py"
TARGET = "123@chatroom"
TS = 1767578400  # 2026-01-05 10:00 local-ish; fixed for the fixture


class NativeBackendTest(unittest.TestCase):
    def make_fixture(self, root: Path):
        work = root / "work"
        account = root / "account"
        for sub in ("session", "contact", "message"):
            (work / sub).mkdir(parents=True)
        (account / "msg" / "video").mkdir(parents=True)
        (account / "msg" / "attach").mkdir(parents=True)

        session = sqlite3.connect(work / "session" / "session.db")
        session.execute("create table SessionTable (username text, last_timestamp integer, summary text)")
        session.execute("insert into SessionTable values (?, ?, ?)", (TARGET, TS, "hello"))
        session.commit()
        session.close()

        contact = sqlite3.connect(work / "contact" / "contact.db")
        contact.execute("create table contact (username text, remark text, nick_name text)")
        contact.execute("insert into contact values ('wxid_me', '我', 'Me')")
        contact.execute("insert into contact values ('wxid_lisi', '李四', 'LS')")
        contact.execute("create table my_user_info (user_name text, nick_name text)")
        contact.execute("insert into my_user_info values ('wxid_me', 'Me')")
        contact.commit()
        contact.close()

        message = sqlite3.connect(work / "message" / "message_0.db")
        message.execute("create table Name2Id (user_name text)")
        message.execute("insert into Name2Id(rowid, user_name) values (1, 'wxid_lisi')")
        table = "Msg_" + hashlib.md5(TARGET.encode()).hexdigest()
        message.execute(
            f'create table "{table}" (local_id integer, server_id text, local_type integer, '
            "real_sender_id integer, create_time integer, message_content blob, packed_info_data blob)"
        )
        message.execute(f'insert into "{table}" values (?, ?, ?, ?, ?, ?, ?)',
                        (1, "srv-1", 1, 1, TS, b"hello", b""))
        message.commit()
        message.close()

        cfg = root / "config.json"
        cfg.write_text(json.dumps({"workDir": str(work), "accountRoot": str(account)}), encoding="utf-8")
        return work, account, cfg

    def run_native(self, root: Path, cfg: Path, *args):
        env = {
            **os.environ,
            "WEFLOW_NATIVE_CONFIG": str(cfg),
            "WEFLOW_NATIVE_WORK": str(root / "work"),
            "WEFLOW_NATIVE_ACCOUNT": str(root / "account"),
            "WEFLOW_NATIVE_PYTHON": sys.executable,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def test_sessions_and_contacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _work, _account, cfg = self.make_fixture(root)
            res = self.run_native(root, cfg, "sessions", "--json", "--limit", "5")
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(data["sessions"][0]["username"], TARGET)
            self.assertEqual(data["sessions"][0]["displayName"], TARGET)
            res = self.run_native(root, cfg, "contacts", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            contacts = json.loads(res.stdout)["contacts"]
            self.assertTrue(any(c["username"] == "wxid_lisi" for c in contacts))

    def test_export_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _work, _account, cfg = self.make_fixture(root)
            out = root / "out"
            res = self.run_native(
                root, cfg, "export", TARGET, "json",
                "--from", "2026-01-05", "--to", "2026-01-05",
                "--output", str(out), "--limit", "0", "--contract", "weflow-v1",
                "--non-interactive", "--json",
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            result = json.loads(res.stdout)
            self.assertTrue(result["success"])
            payload = json.loads((out / f"{TARGET}_messages.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["messages"][0]["localId"], 1)
            self.assertEqual(payload["messages"][0]["serverId"], "srv-1")
            self.assertEqual(payload["coverage"]["boundary"],
                             {"createTime": TS, "localId": 1})
            self.assertEqual(payload["messages"][0]["rawContent"], "hello")
            self.assertEqual(payload["messages"][0]["parsedContent"], "hello")
            self.assertEqual(payload["messages"][0]["isSend"], 0)

    def test_export_quote_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, _account, cfg = self.make_fixture(root)
            table = "Msg_" + hashlib.md5(TARGET.encode()).hexdigest()
            quote_xml = (
                "<msg><appmsg><title>回复：你好</title><type>57</type>"
                "<refermsg><displayname>张三</displayname><type>1</type>"
                "<content>原来的消息</content></refermsg></appmsg></msg>"
            )
            history_xml = (
                "<msg><appmsg><title>群聊的聊天记录</title><type>19</type>"
                "<recorditem><![CDATA[<recordinfo><datalist>"
                '<dataitem datatype="1"><sourcename>张三</sourcename>'
                "<sourcetime>2026-01-05 10:00</sourcetime><datadesc>你好</datadesc></dataitem>"
                '<dataitem datatype="8"><sourcename>李四</sourcename>'
                "<sourcetime>2026-01-05 10:01</sourcetime><datatitle>报告.pdf</datatitle>"
                "<datasize>1024</datasize></dataitem>"
                "</datalist></recordinfo>]]></recorditem></appmsg></msg>"
            )
            conn = sqlite3.connect(work / "message" / "message_0.db")
            conn.execute(
                f'insert into "{table}" values (?, ?, ?, ?, ?, ?, ?)',
                (2, "srv-2", (57 << 32) | 49, 1, TS + 10, quote_xml.encode("utf-8"), b""))
            conn.execute(
                f'insert into "{table}" values (?, ?, ?, ?, ?, ?, ?)',
                (3, "srv-3", (19 << 32) | 49, 1, TS + 20, history_xml.encode("utf-8"), b""))
            conn.commit()
            conn.close()

            out = root / "out"
            res = self.run_native(
                root, cfg, "export", TARGET, "json",
                "--from", "2026-01-05", "--to", "2026-01-05",
                "--output", str(out), "--limit", "0", "--contract", "weflow-v1",
                "--non-interactive", "--json",
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads((out / f"{TARGET}_messages.json").read_text(encoding="utf-8"))
            by_id = {item["localId"]: item for item in payload["messages"]}

            self.assertEqual(by_id[2]["messageType"], "quote")
            self.assertEqual(by_id[2]["quote"]["sender"], "张三")
            self.assertEqual(by_id[2]["quote"]["text"], "原来的消息")

            self.assertEqual(by_id[3]["messageType"], "chat_history")
            records = by_id[3]["records"]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["kind"], "text")
            self.assertEqual(records[0]["sender"], "张三")
            self.assertEqual(records[0]["text"], "你好")
            self.assertEqual(records[1]["kind"], "file")
            self.assertEqual(records[1]["text"], "报告.pdf")

    def test_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _work, _account, cfg = self.make_fixture(root)
            res = self.run_native(root, cfg, "check", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertTrue(data["success"])
            self.assertTrue(data["configuration"]["messageDatabase"])
            self.assertTrue(data["dependencies"]["required"]["zstandard"])


if __name__ == "__main__":
    unittest.main()
