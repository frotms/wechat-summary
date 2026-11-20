# -*- coding: utf-8 -*-
"""Structured errors from the 4.1 decrypt helper."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wechat41-decrypt.py"

try:
    import sqlcipher3  # noqa: F401
    HAS_SQLCIPHER = True
except Exception:
    HAS_SQLCIPHER = False


@unittest.skipUnless(HAS_SQLCIPHER, "sqlcipher3 not installed")
class WeChat41ErrorTest(unittest.TestCase):
    def run_decrypt(self, config: Path, work: Path):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(config),
             "--work-dir", str(work), "--json"],
            capture_output=True, text=True, encoding="utf-8", env=env)

    def test_missing_config_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            res = self.run_decrypt(config, root / "work")
            self.assertEqual(res.returncode, 3, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["code"], "CONFIG_INVALID")
            self.assertIn("hint", payload)

    def test_missing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = root / "account"
            (account / "db_storage").mkdir(parents=True)
            config = root / "config.json"
            config.write_text(json.dumps({"dbPath": str(account)}), encoding="utf-8")
            res = self.run_decrypt(config, root / "work")
            self.assertEqual(res.returncode, 3, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["code"], "CONFIG_KEY_MISSING")
            self.assertIn("hint", payload)

    def test_work_dir_inside_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = root / "account"
            (account / "db_storage").mkdir(parents=True)
            config = root / "config.json"
            config.write_text(json.dumps({"dbPath": str(account)}), encoding="utf-8")
            res = self.run_decrypt(config, account / "work")
            self.assertEqual(res.returncode, 3, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["code"], "WORK_DIR_INSIDE_ACCOUNT")


if __name__ == "__main__":
    unittest.main()
