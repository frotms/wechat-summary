# -*- coding: utf-8 -*-
"""Tests for contacts/group/avatar export."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wechat-contacts-export.py"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\xff\xd9"


class ContactsExportTest(unittest.TestCase):
    def make_work(self, root: Path):
        work = root / "work"
        (work / "contact").mkdir(parents=True)
        (work / "head_image").mkdir(parents=True)
        conn = sqlite3.connect(work / "contact" / "contact.db")
        conn.execute(
            "create table contact(id integer primary key, username text, local_type integer, "
            "alias text, remark text, nick_name text, big_head_url text, small_head_url text, "
            "head_img_md5 text, delete_flag integer, description text)")
        conn.executemany("insert into contact values (?,?,?,?,?,?,?,?,?,?,?)", [
            (1, "wxid_a", 1, "alice", "Alice备注", "AliceNick", "", "", "", 0, ""),
            (2, "wxid_b", 1, "", "", "Bob", "", "", "", 0, ""),
            (3, "123@chatroom", 2, "", "", "测试群", "", "", "", 0, ""),
            (4, "gh_news", 3, "", "", "公众号", "", "", "", 0, ""),
        ])
        conn.execute("create table chat_room(id integer primary key, username text, owner text, ext_buffer blob)")
        conn.execute("insert into chat_room values (3, '123@chatroom', 'wxid_a', null)")
        conn.execute("create table chatroom_member(room_id integer, member_id integer)")
        conn.execute("insert into chatroom_member values (3, 1)")
        conn.execute("insert into chatroom_member values (3, 2)")
        conn.commit()
        conn.close()

        conn = sqlite3.connect(work / "head_image" / "head_image.db")
        conn.execute("create table head_image(username text primary key, md5 text, image_buffer blob, update_time integer)")
        conn.execute("insert into head_image values ('wxid_a', 'abcdef0123456789', ?, 0)", (JPEG,))
        conn.commit()
        conn.close()
        return work

    def run_export(self, work: Path, output: Path, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--work-dir", str(work),
             "--output", str(output), *args],
            capture_output=True, text=True, encoding="utf-8", env=env)

    def test_exports_contacts_groups_and_avatars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self.make_work(root)
            out = root / "out"
            res = self.run_export(work, out)
            self.assertEqual(res.returncode, 0, res.stderr)

            contacts = json.loads((out / "contacts.json").read_text(encoding="utf-8"))
            groups = json.loads((out / "groups.json").read_text(encoding="utf-8"))
            self.assertEqual(contacts["counts"]["contacts"], 4)
            self.assertEqual(contacts["counts"]["groups"], 1)
            self.assertEqual(contacts["counts"]["avatars"], 1)
            by_name = {item["username"]: item for item in contacts["contacts"]}
            self.assertEqual(by_name["wxid_a"]["displayName"], "Alice备注")
            self.assertEqual(by_name["wxid_a"]["type"], "person")
            self.assertEqual(by_name["123@chatroom"]["type"], "group")
            self.assertEqual(by_name["gh_news"]["type"], "official")
            self.assertTrue(by_name["wxid_a"]["headImage"].startswith("avatars/"))
            avatar = out / by_name["wxid_a"]["headImage"]
            self.assertTrue(avatar.is_file())
            self.assertTrue(avatar.read_bytes().startswith(b"\xff\xd8\xff"))

            self.assertEqual(groups["groups"][0]["memberCount"], 2)
            self.assertEqual(groups["groups"][0]["owner"], "wxid_a")
            self.assertEqual(len(groups["groups"][0]["members"]), 2)

    def test_no_avatars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self.make_work(root)
            out = root / "out"
            res = self.run_export(work, out, "--no-avatars")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse((out / "avatars").exists())
            contacts = json.loads((out / "contacts.json").read_text(encoding="utf-8"))
            self.assertEqual(contacts["counts"]["avatars"], 0)


if __name__ == "__main__":
    unittest.main()
