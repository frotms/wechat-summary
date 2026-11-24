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
from datetime import datetime
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
    def make_export(self, root: Path, target: str, rows, exported_rows, skipped=0,
                    query=None, raw_rows=None, boundary="db-max", generated_at=None):
        query = query if query is not None else {"date": "2026-01-05"}
        normalized = []
        for row in rows:
            if len(row) == 4:
                local_id, server_id, local_type, create_time = row
            else:
                local_id, local_type, create_time = row
                server_id = ""
            normalized.append((local_id, server_id, local_type, create_time))
        raw_rows = normalized if raw_rows is None else raw_rows

        work = root / "work"
        (work / "message").mkdir(parents=True)
        db = work / "message" / "message_0.db"
        conn = sqlite3.connect(db)
        table = "Msg_" + hashlib.md5(target.encode()).hexdigest()
        conn.execute(
            f'create table "{table}" (local_id integer, server_id text, '
            "local_type integer, create_time integer)")
        conn.executemany(
            f'insert into "{table}" (local_id, server_id, local_type, create_time) '
            "values (?, ?, ?, ?)", normalized)
        conn.commit()
        conn.close()

        if boundary == "db-max":
            boundary = max(
                ((create_time, local_id) for local_id, _sid, _t, create_time in normalized),
                default=None)

        output = root / "out"
        bundle_dir = output / "bundle"
        target_obj = {"id": target, "name": "测试会话"}
        bundle_manifest = {"query": query}
        if generated_at:
            bundle_manifest["generatedAt"] = generated_at
        write_json(bundle_dir / "manifest.json", bundle_manifest)
        write_json(output / "manifest.json", {
            "query": query,
            "bundles": [{"dir": "bundle", "target": target_obj,
                         "stats": {"skippedByType": skipped}}],
        })
        raw = [
            {"localId": local_id, "serverId": server_id, "createTime": create_time}
            for local_id, server_id, _t, create_time in raw_rows
        ]
        coverage = {"mayHaveMore": False}
        if boundary:
            coverage["boundary"] = {"createTime": boundary[0], "localId": boundary[1]}
        write_json(bundle_dir / "raw" / f"{target}_messages.json", {
            "messages": raw, "coverage": coverage,
        })
        with (bundle_dir / "messages.jsonl").open("w", encoding="utf-8") as handle:
            for record in exported_rows:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return output, bundle_dir, query, target_obj

    def deep_result(self, root: Path, bundle_dir: Path, target: str, query: dict,
                    skipped: int = 0):
        records = audit.read_jsonl(bundle_dir / "messages.jsonl")
        raw, coverage, _name = audit.raw_messages(bundle_dir, target)
        return audit.deep_check(
            bundle_dir, target, query, root / "work", records, raw, coverage, skipped)

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

    # ---------- --deep: exact (local_id, server_id) reconciliation ----------

    def test_deep_complete_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10), (2, "s2", 3, DAY + 20), (3, "s3", 1, DAY + 30)]
            exported = [
                {"id": "1:%d" % (DAY + 10), "localId": 1, "serverId": "s1",
                 "type": "text", "timestamp": DAY + 10, "media": {}},
                {"id": "3:%d" % (DAY + 30), "localId": 3, "serverId": "s3",
                 "type": "text", "timestamp": DAY + 30, "media": {}},
            ]
            query = {"date": "2026-01-05", "types": ["text"]}
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=1, query=query)
            result = audit.audit_bundle(
                bundle_dir, {"target": target_obj, "stats": {"skippedByType": 1}},
                query, {"kind": "work", "work_dir": str(root / "work"), "deep": True})
            self.assertTrue(result["deep"]["ok"])
            self.assertTrue(result["deep"]["checks"]["sourceIdsMatchRaw"])
            self.assertTrue(result["deep"]["checks"]["exportedIdsInRaw"])
            self.assertTrue(result["deep"]["checks"]["rawNotExportedMatchesSkipped"])
            self.assertEqual(result["deep"]["source"]["messages"], 3)
            self.assertEqual(result["deep"]["raw"]["messages"], 3)
            self.assertTrue(result["complete"])

    def test_deep_catches_count_equal_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10), (2, "s2", 1, DAY + 20)]
            # Counts all match: DB 2 == raw 2 == exported 1 + skipped 1.
            # The exported id is not in the raw JSON.
            exported = [{"id": "99:%d" % (DAY + 10), "localId": 99, "serverId": "s99",
                         "type": "text", "timestamp": DAY + 10, "media": {}}]
            query = {"date": "2026-01-05", "types": ["text"]}
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=1, query=query)
            result = audit.audit_bundle(
                bundle_dir, {"target": target_obj, "stats": {"skippedByType": 1}},
                query, {"kind": "work", "work_dir": str(root / "work"), "deep": True})
            self.assertTrue(result["checks"]["sourceMatchesRaw"])
            self.assertTrue(result["export"]["chainMatchesRaw"])
            self.assertFalse(result["deep"]["checks"]["exportedIdsInRaw"])
            self.assertEqual(result["deep"]["exportedNotInRawCount"], 1)
            self.assertFalse(result["complete"])

    def test_deep_detects_missing_trailing_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10), (2, "s2", 1, DAY + 20)]
            raw_rows = rows[:1]
            exported = [{"id": "1:%d" % (DAY + 10), "localId": 1, "serverId": "s1",
                         "type": "text", "timestamp": DAY + 10, "media": {}}]
            query = {"date": "2026-01-05", "types": ["text"]}
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=0, query=query, raw_rows=raw_rows)
            deep = self.deep_result(root, bundle_dir, target, query)
            self.assertFalse(deep["checks"]["sourceIdsMatchRaw"])
            self.assertEqual(deep["missingCount"], 1)
            self.assertEqual(deep["missingIds"], ["2:s2"])

    def test_audit_uses_boundary_tolerance_for_same_second_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [
                (1, "s1", 1, DAY + 10),
                (2, "s2", 1, DAY + 10),  # arrived after the export edge
                (3, "s3", 1, DAY + 20),
            ]
            raw_rows = rows[:1]
            exported = [{"id": "1:%d" % (DAY + 10), "localId": 1, "serverId": "s1",
                         "type": "text", "timestamp": DAY + 10, "media": {}}]
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=0,
                raw_rows=raw_rows, boundary=(DAY + 10, 1))
            result = audit.audit_bundle(
                bundle_dir, {"target": target_obj, "stats": {"skippedByType": 0}},
                query, {"kind": "work", "work_dir": str(root / "work")})
            self.assertTrue(result["complete"], result)
            self.assertEqual(result["source"]["dbMessages"], 1)
            self.assertTrue(result["checks"]["boundaryTolerance"]["used"])
            self.assertEqual(result["checks"]["boundaryTolerance"]["tail"], 1)

    def test_deep_respects_export_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10), (2, "s2", 1, DAY + 20)]
            # The second message arrived after the export: boundary pins id 1.
            raw_rows = rows[:1]
            exported = [{"id": "1:%d" % (DAY + 10), "localId": 1, "serverId": "s1",
                         "type": "text", "timestamp": DAY + 10, "media": {}}]
            query = {"date": "2026-01-05", "types": ["text"]}
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=0, query=query,
                raw_rows=raw_rows, boundary=(DAY + 10, 1))
            deep = self.deep_result(root, bundle_dir, target, query)
            self.assertTrue(deep["ok"], deep)
            self.assertEqual(deep["boundary"]["source"], "raw-coverage")
            self.assertEqual(deep["source"]["messages"], 1)

    def test_deep_limit_uses_the_same_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10), (2, "s2", 1, DAY + 20)]
            raw_rows = rows[1:]
            exported = [{"id": "2:%d" % (DAY + 20), "localId": 2, "serverId": "s2",
                         "type": "text", "timestamp": DAY + 20, "media": {}}]
            query = {"date": "2026-01-05", "types": ["text"], "limit": 1}
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=0, query=query,
                raw_rows=raw_rows, boundary=(DAY + 20, 2))
            deep = self.deep_result(root, bundle_dir, target, query)
            self.assertTrue(deep["ok"], deep)
            self.assertEqual(deep["source"]["messages"], 1)
            self.assertEqual(deep["missingCount"], 0)

    def test_deep_generated_at_fallback_excludes_later_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10)]
            query = {"date": "2026-01-05", "types": ["text"]}
            generated = datetime.fromtimestamp(DAY + 5).astimezone().isoformat()
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, [], skipped=0, query=query,
                raw_rows=[], boundary=None, generated_at=generated)
            deep = self.deep_result(root, bundle_dir, target, query)
            self.assertTrue(deep["ok"], deep)
            self.assertEqual(deep["boundary"]["source"], "generatedAt")
            self.assertFalse(deep["boundary"]["reliable"])

    def test_deep_type_mismatch_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10), (2, "s2", 3, DAY + 20)]
            # `--types text` but the image was exported and the text skipped.
            exported = [{"id": "2:%d" % (DAY + 20), "localId": 2, "serverId": "s2",
                         "type": "image", "timestamp": DAY + 20, "media": {}}]
            query = {"date": "2026-01-05", "types": ["text"]}
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=1, query=query)
            deep = self.deep_result(root, bundle_dir, target, query, skipped=1)
            self.assertFalse(deep["checks"]["typesMatchQuery"])
            self.assertEqual(deep["typeMismatchCount"], 1)
            self.assertFalse(deep["ok"])

    def test_deep_missing_ids_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "123@chatroom"
            rows = [(1, "s1", 1, DAY + 10)]
            # Old exports may omit localId/serverId; deep must say so instead of guessing.
            exported = [{"id": "1:%d" % (DAY + 10), "type": "text",
                         "timestamp": DAY + 10, "media": {}}]
            query = {"date": "2026-01-05", "types": ["text"]}
            output, bundle_dir, query, target_obj = self.make_export(
                root, target, rows, exported, skipped=0, query=query)
            deep = self.deep_result(root, bundle_dir, target, query)
            self.assertFalse(deep["checks"]["idsAvailable"])
            self.assertFalse(deep["ok"])


if __name__ == "__main__":
    unittest.main()
