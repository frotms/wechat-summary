# -*- coding: utf-8 -*-
"""Tests for the export completeness audit."""
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("wechat_audit", ROOT / "scripts" / "wechat-audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

DAY = 1767571200  # 2026-01-05 00:00:00 UTC; tests use a fixed window below


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class AuditTest(unittest.TestCase):
    def make_export(self, root: Path, target: str, rows, exported_rows, skipped=0):
        work = root / "work"
        (work / "message").mkdir(parents=True)
        db = work / "message" / "message_0.db"
        conn = sqlite3.connect(db)
        table = "Msg_" + hashlib.md5(target.encode()).hexdigest()
        conn.execute(f'create table "{table}" (local_id integer, local_type integer, create_time integer)')
        conn.executemany(f'insert into "{table}" values (?, ?, ?)', rows)
        conn.commit()
        conn.close()

        output = root / "out"
        bundle_dir = output / "bundle"
        target_obj = {"id": target, "name": "测试会话"}
        query = {"date": "2026-01-05"}
        write_json(bundle_dir / "manifest.json", {"query": query})
        write_json(output / "manifest.json", {
            "query": query,
            "bundles": [{"dir": "bundle", "target": target_obj, "stats": {"skippedByType": skipped}}],
        })
        raw = [{"localId": local_id, "createTime": create_time} for local_id, _t, create_time in rows]
        write_json(bundle_dir / "raw" / f"{target}_messages.json", {
            "messages": raw, "coverage": {"mayHaveMore": False},
        })
        with (bundle_dir / "messages.jsonl").open("w", encoding="utf-8") as handle:
            for record in exported_rows:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return output, bundle_dir, query, target_obj

    def test_complete_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, 1, DAY + 10), (2, 3, DAY + 20), (3, 1, DAY + 30)]
            exported = [
                {"id": "1:%d" % (DAY + 10), "timestamp": DAY + 10, "media": {}},
                {"id": "3:%d" % (DAY + 30), "timestamp": DAY + 30, "media": {}},
            ]
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=1)
            result = audit.audit_bundle(
                bundle_dir, {"target": target_obj, "stats": {"skippedByType": 1}},
                query, {"kind": "work", "work_dir": str(root / "work")})
            self.assertEqual(result["source"]["dbMessages"], 3)
            self.assertEqual(result["source"]["rawMessages"], 3)
            self.assertEqual(result["export"]["exportedMessages"], 2)
            self.assertEqual(result["export"]["skippedByType"], 1)
            self.assertTrue(result["export"]["chainMatchesRaw"])
            self.assertTrue(result["checks"]["sourceMatchesRaw"])
            self.assertTrue(result["complete"])

    def test_missing_exported_row_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, 1, DAY + 10), (2, 1, DAY + 20)]
            exported = [{"id": "1:%d" % (DAY + 10), "timestamp": DAY + 10, "media": {}}]
            output, bundle_dir, query, target_obj = self.make_export(root, target, rows, exported)
            result = audit.audit_bundle(
                bundle_dir, {"target": target_obj, "stats": {"skippedByType": 0}},
                query, {"kind": "work", "work_dir": str(root / "work")})
            self.assertFalse(result["complete"])
            self.assertFalse(result["export"]["chainMatchesRaw"])

    def test_database_mismatch_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, 1, DAY + 10), (2, 1, DAY + 20)]
            exported = [{"id": "1:%d" % (DAY + 10), "timestamp": DAY + 10, "media": {}}]
            output, bundle_dir, query, target_obj = self.make_export(root, target, rows, exported)
            # Raw JSON only has one message although the DB has two: weflow
            # silently dropped one row.
            write_json(bundle_dir / "raw" / f"{target}_messages.json", {
                "messages": [{"localId": 1, "createTime": DAY + 10}],
                "coverage": {"mayHaveMore": False},
            })
            result = audit.audit_bundle(
                bundle_dir, {"target": target_obj, "stats": {"skippedByType": 0}},
                query, {"kind": "work", "work_dir": str(root / "work")})
            self.assertFalse(result["complete"])
            self.assertFalse(result["checks"]["sourceMatchesRaw"])

    def test_outside_window_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, 1, DAY + 10)]
            exported = [{"id": "1:%d" % (DAY + 10), "timestamp": DAY + 864000, "media": {}}]
            output, bundle_dir, query, target_obj = self.make_export(root, target, rows, exported)
            result = audit.audit_bundle(
                bundle_dir, {"target": target_obj, "stats": {"skippedByType": 0}},
                query, {"kind": "work", "work_dir": str(root / "work")})
            self.assertEqual(result["checks"]["outsideWindow"], 1)
            self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
