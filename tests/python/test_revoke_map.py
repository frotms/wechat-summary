# -*- coding: utf-8 -*-
"""Tests for the revoke-trace map."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wechat-revoke-map.py"
TARGET = "123@chatroom"
TS = int(datetime(2026, 1, 5, 10, 0).timestamp())
DAY = "2026-01-05"


class RevokeMapTest(unittest.TestCase):
    def make_work(self, root: Path):
        work = root / "work"
        (work / "general").mkdir(parents=True)
        (work / "message").mkdir(parents=True)

        general = sqlite3.connect(work / "general" / "general.db")
        general.execute("create table revokebatchmessage (local_id integer, batch_id integer, "
                        "msg_unique_id text, session_name text, msg_local_id integer, msg_create_time integer)")
        general.execute("create table revokemessage (to_user_name text, svr_id text, message_type integer, "
                        "revoke_time integer, content text, at_user_list text)")
        general.execute("insert into revokebatchmessage values (1, 999, ?, ?, 23, ?)",
                        (f"{TARGET}_{TS}_23", TARGET, TS))
        general.execute("insert into revokemessage values (?, 'srv-single', 1, ?, 'single original', '')",
                        (TARGET, TS))
        general.execute("insert into revokemessage values (?, 'srv-orphan', 1, ?, 'orphan original', '')",
                        (TARGET, TS))
        general.commit()
        general.close()

        message = sqlite3.connect(work / "message" / "message_0.db")
        table = "Msg_" + hashlib.md5(TARGET.encode()).hexdigest()
        message.execute(f'create table "{table}" (local_id integer, server_id text, '
                        f'create_time integer, local_type integer)')
        message.executemany(f'insert into "{table}" values (?, ?, ?, ?)', [
            (23, "srv-batch", TS, 1),
            (30, "srv-single", TS + 60, 1),
        ])
        message.commit()
        message.close()
        return work

    def run_map(self, work: Path, output: Path):
        res = subprocess.run([
            sys.executable, str(SCRIPT),
            "--work-dir", str(work),
            "--target", TARGET,
            "--from", DAY, "--to", DAY,
            "--out", str(output),
        ], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_batch_match_and_single_match_and_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self.make_work(root)
            output = root / "revoke-map.json"
            data = self.run_map(work, output)
            self.assertEqual(data["counts"], {
                "batch": 1, "single": 1, "matched": 2, "orphans": 1,
            })
            batch = data["items"][f"23:{TS}"]
            self.assertEqual(batch["kind"], "batch")
            self.assertEqual(batch["batch_id"], 999)
            self.assertEqual(batch["server_id"], "srv-batch")
            single = data["items"][f"30:{TS + 60}"]
            self.assertEqual(single["kind"], "single")
            self.assertEqual(single["content"], "single original")
            self.assertEqual(data["orphans"][0]["content"], "orphan original")
            self.assertEqual(data["orphans"][0]["target"], TARGET)

    def test_time_window_excludes_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self.make_work(root)
            output = root / "revoke-map.json"
            res = subprocess.run([
                sys.executable, str(SCRIPT),
                "--work-dir", str(work),
                "--target", TARGET,
                "--from", "2026-01-06", "--to", "2026-01-06",
                "--out", str(output),
            ], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["counts"]["matched"], 0)
            self.assertEqual(data["counts"]["orphans"], 0)


if __name__ == "__main__":
    unittest.main()
