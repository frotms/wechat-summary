# -*- coding: utf-8 -*-
"""End-to-end setup test for native-only 4.2 work/ and key cache reuse."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wechat42-sandbox.py"


class SandboxSetupTest(unittest.TestCase):
    def make_tool(self, root: Path) -> Path:
        tool = root / "fake-wechat-exporter"
        (tool / "app" / "core").mkdir(parents=True)
        (tool / "app" / "core" / "keys.py").write_text(
            "def extract_keys(data_root):\n"
            "    return ({'message/message_0.db': {'enc_key': 'ab' * 32, 'salt': 'cd' * 16}},\n"
            "            {'mode': 'fake-process', 'source': 'fake'})\n",
            encoding="utf-8")
        (tool / "app" / "core" / "decrypt.py").write_text(
            "import os, shutil\n"
            "from pathlib import Path\n"
            "def decrypt_all(data_root, work_dir, keys, skip_fresh=False, progress=None):\n"
            "    if os.environ.get('FAKE_DECRYPT_FAIL') == '1':\n"
            "        return {'ok': 0, 'failed': len(keys), 'total': len(keys)}\n"
            "    ok = 0\n"
            "    for rel in keys:\n"
            "        src = Path(data_root) / rel\n"
            "        dst = Path(work_dir) / rel\n"
            "        dst.parent.mkdir(parents=True, exist_ok=True)\n"
            "        shutil.copy2(src, dst)\n"
            "        ok += 1\n"
            "    return {'ok': ok, 'failed': 0, 'total': len(keys)}\n",
            encoding="utf-8")
        return tool

    def make_account(self, root: Path) -> Path:
        account = root / "account"
        db = account / "db_storage" / "message" / "message_0.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"\x00" * 8192)
        (account / "msg").mkdir(parents=True)
        return account

    def run_setup(self, root: Path, tool: Path, account: Path, *extra, env=None):
        paths = {
            "work": root / "work",
            "sandbox": root / "sandbox",
            "home": root / "home",
            "keys": root / "keys.json",
        }
        args = [
            sys.executable, str(SCRIPT),
            "--tool-dir", str(tool),
            "--data-root", str(account / "db_storage"),
            "--account-root", str(account),
            "--work-dir", str(paths["work"]),
            "--sandbox-dir", str(paths["sandbox"]),
            "--home-dir", str(paths["home"]),
            "--keys-file", str(paths["keys"]),
            "--no-sandbox",
            *extra,
        ]
        proc_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        if env:
            proc_env.update(env)
        res = subprocess.run(args, capture_output=True, text=True,
                             encoding="utf-8", env=proc_env)
        return res, paths

    def test_native_only_saves_and_reuses_key_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = self.make_tool(root)
            account = self.make_account(root)

            first, paths = self.run_setup(root, tool, account)
            self.assertEqual(first.returncode, 0, first.stderr)
            data = json.loads(first.stdout)
            self.assertEqual(data["mode"], "native-only")
            self.assertEqual(data["keys"]["source"], "process")
            self.assertTrue(data["keys"]["saved"])
            self.assertTrue(paths["keys"].is_file())
            self.assertTrue((paths["work"] / "message" / "message_0.db").is_file())

            # Remove work/ but keep keys.json: the next setup must use the cache.
            shutil.rmtree(paths["work"])
            second, paths = self.run_setup(root, tool, account)
            self.assertEqual(second.returncode, 0, second.stderr)
            data = json.loads(second.stdout)
            self.assertEqual(data["keys"]["source"], "cache")
            self.assertEqual(data["decrypt"]["ok"], 1)
            self.assertTrue((paths["work"] / "message" / "message_0.db").is_file())

    def test_failed_cache_falls_back_to_process_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = self.make_tool(root)
            account = self.make_account(root)

            first, _paths = self.run_setup(root, tool, account)
            self.assertEqual(first.returncode, 0, first.stderr)

            second, _paths = self.run_setup(
                root, tool, account, env={"FAKE_DECRYPT_FAIL": "1"})
            self.assertEqual(second.returncode, 0, second.stderr)
            data = json.loads(second.stdout)
            self.assertEqual(data["keys"]["source"], "process")
            self.assertIn("重新从进程提取", second.stderr)


if __name__ == "__main__":
    unittest.main()
