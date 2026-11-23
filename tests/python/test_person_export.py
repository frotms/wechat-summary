# -*- coding: utf-8 -*-
"""Tests for cross-session person/timeline helpers."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "wechat_person_export", ROOT / "scripts" / "wechat-person-export.py")
person = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(person)


class PersonExportTest(unittest.TestCase):
    def test_sender_matches(self):
        msg = {"sender": "张三", "sender_wxid": "wxid_zs"}
        self.assertTrue(person.sender_matches(msg, "张三"))
        self.assertTrue(person.sender_matches(msg, "wxid_zs"))
        self.assertTrue(person.sender_matches(msg, ""))
        self.assertFalse(person.sender_matches(msg, "李四"))

    def test_record_and_state(self):
        msg = {"ts": 1767578400, "time": "2026-01-05 10:00",
               "sender": "张三", "sender_wxid": "wxid_zs",
               "kind": "text", "local_id": 1, "server_id": "s1", "text": "你好"}
        row = person.record_of({"name": "项目群", "is_group": True}, msg, "123@chatroom")
        self.assertEqual(row["session"], "项目群")
        self.assertEqual(row["senderUsername"], "wxid_zs")
        self.assertEqual(row["type"], "text")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            person.save_state(path, 1767578400)
            self.assertEqual(person.load_state(path)["lastTimestamp"], 1767578400)


if __name__ == "__main__":
    unittest.main()
