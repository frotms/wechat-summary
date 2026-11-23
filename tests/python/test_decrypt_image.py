# -*- coding: utf-8 -*-
"""Tests for the single-file image decryptor."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util import Padding

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wechat-decrypt-image.py"


class DecryptImageTest(unittest.TestCase):
    def make_v2(self, plain: bytes):
        key = b"0123456789abcdef"
        aes_size, xor_size = 24, 4
        encrypted = AES.new(key, AES.MODE_ECB).encrypt(
            Padding.pad(plain[:aes_size], 16))
        tail = bytes(b ^ 0x5A for b in plain[-xor_size:])
        payload = (b"\x07\x08V2\x08\x07" + struct.pack("<LLx", aes_size, xor_size)
                   + encrypted + plain[aes_size:-xor_size] + tail)
        return payload

    def run_tool(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def test_decode_with_explicit_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = b"\x89PNG\r\n\x1a\n" + os.urandom(80)
            src = root / "image.dat"
            src.write_bytes(self.make_v2(plain))
            out = root / "decoded.png"
            res = self.run_tool(str(src), "--output", str(out),
                                "--aes", "0123456789abcdef", "--xor", "0x5A", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(out.read_bytes(), plain)
            summary = json.loads(res.stdout)
            self.assertEqual(summary["format"], "png")
            self.assertEqual(summary["keySource"], "arguments")

    def test_missing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "image.dat"
            src.write_bytes(b"not-a-v2-file")
            res = self.run_tool(str(src), "--output", str(root / "out.png"), "--json")
            self.assertEqual(res.returncode, 3)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["code"], "IMAGE_KEY_MISSING")


if __name__ == "__main__":
    unittest.main()
