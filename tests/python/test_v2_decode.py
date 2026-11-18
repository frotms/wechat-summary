import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib.v2_decode import (  # noqa: E402
    MAGIC, clean_account_id, decode_v2_bytes, decode_v2_file, looks_like_video,
)
from Crypto.Cipher import AES  # noqa: E402
from Crypto.Util import Padding  # noqa: E402

import hashlib


class V2DecodeTest(unittest.TestCase):
    def test_clean_account_id(self):
        self.assertEqual(clean_account_id('mat_tranmer_3f87'), 'mat_tranmer')
        self.assertEqual(clean_account_id('wxid_plain'), 'wxid_plain')

    def test_decode_synthetic_video(self):
        code = '12345'
        wxid = 'mat_tranmer'
        aes_key = hashlib.md5(f'{code}{wxid}'.encode()).hexdigest()[:16].encode()
        xor_key = int(code) & 0xFF
        plain = b'\x00\x00\x00\x18ftypmp42' + os.urandom(80)
        aes_size = 32
        xor_size = 8
        head = plain[:aes_size]
        middle = plain[aes_size:-xor_size]
        tail = plain[-xor_size:]
        encrypted = AES.new(aes_key, AES.MODE_ECB).encrypt(Padding.pad(head, 16))
        tail_x = bytes(value ^ xor_key for value in tail)
        data = MAGIC + struct.pack('<LLx', aes_size, xor_size) + encrypted + middle + tail_x

        decoded = decode_v2_bytes(data, aes_key, xor_key)
        self.assertIsNotNone(decoded)
        self.assertTrue(looks_like_video(decoded))
        self.assertEqual(decoded, plain)

    def test_decode_file(self):
        code = '777'
        wxid = 'wxid_demo'
        aes_key = hashlib.md5(f'{code}{wxid}'.encode()).hexdigest()[:16].encode()
        xor_key = int(code) & 0xFF
        plain = b'\x00\x00\x00\x18ftypmp42' + os.urandom(64)
        aes_size = 24
        xor_size = 4
        encrypted = AES.new(aes_key, AES.MODE_ECB).encrypt(Padding.pad(plain[:aes_size], 16))
        tail_x = bytes(value ^ xor_key for value in plain[-xor_size:])
        data = MAGIC + struct.pack('<LLx', aes_size, xor_size) + encrypted + plain[aes_size:-xor_size] + tail_x
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'sample.mp4'
            src.write_bytes(data)
            out = decode_v2_file(str(src), str(Path(tmp) / 'out'), wxid, [code])
            self.assertIsNotNone(out)
            self.assertTrue(looks_like_video(Path(out).read_bytes()))


if __name__ == '__main__':
    unittest.main()
