# -*- coding: utf-8 -*-
"""Tests for the custom-sticker (表情包) decrypt/decode helpers."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from lib.sticker import (  # noqa: E402
    account_wxid,
    derive_key,
    decrypt,
    find_sticker_file,
    prepare_sticker,
    sniff,
    sticker_cache_dirs,
)

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    HAVE_CRYPTO = True
except ImportError:  # pragma: no cover
    HAVE_CRYPTO = False

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


def encrypt(raw: bytes, key: bytes) -> bytes:
    """The inverse of `decrypt`: AES-128-CBC(key=IV) with PKCS7."""
    pad = 16 - (len(raw) % 16)
    body = raw + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    return enc.update(body) + enc.finalize()


class StickerTest(unittest.TestCase):
    def test_derive_key_is_16_bytes(self):
        key = derive_key("206269640", "wxid_test")
        self.assertEqual(len(key), 16)
        self.assertEqual(key, derive_key("206269640", "wxid_test"))
        self.assertNotEqual(key, derive_key("206269641", "wxid_test"))
        self.assertNotEqual(key, derive_key("206269640", "wxid_other"))

    def test_account_wxid_strips_the_suffix(self):
        self.assertEqual(account_wxid("wxid_abc_1234"), "wxid_abc")
        self.assertEqual(account_wxid("wxid_abc"), "wxid_abc")

    def test_sniff_detects_image_formats(self):
        self.assertEqual(sniff(PNG)[0], "image/png")
        self.assertEqual(sniff(b"\xff\xd8\xff\xe0")[0], "image/jpeg")
        self.assertEqual(sniff(b"GIF89a")[0], "image/gif")
        self.assertEqual(sniff(b"wxgf\x12\x00")[0], "image/hevc")
        self.assertEqual(sniff(b"random bytes")[0], None)

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
    def test_decrypt_reverses_the_encryption(self):
        key = derive_key("12345", "wxid_test")
        raw = PNG + b"\x00" * 100
        decrypted = decrypt(encrypt(raw, key), key)
        self.assertEqual(decrypted, raw)

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
    def test_decrypt_rejects_a_wrong_key(self):
        key = derive_key("12345", "wxid_test")
        wrong = derive_key("99999", "wxid_test")
        raw = PNG + b"\x00" * 100
        self.assertNotEqual(decrypt(encrypt(raw, key), wrong), raw)

    def test_sticker_cache_dirs_are_collected(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "cache", "2026-10", "Emoticon"))
            os.makedirs(os.path.join(root, "business", "emoticon", "Persist"))
            dirs = sticker_cache_dirs(root)
            self.assertTrue(any(d.endswith(os.path.join("2026-10", "Emoticon")) for d in dirs))
            self.assertTrue(any(d.endswith("Persist") for d in dirs))

    def test_find_sticker_file_prefers_the_plain_name(self):
        with tempfile.TemporaryDirectory() as root:
            base = os.path.join(root, "business", "emoticon", "Persist")
            os.makedirs(os.path.join(base, "ab"))
            md5 = "ab" + "c" * 30
            with open(os.path.join(base, "ab", md5), "wb") as handle:
                handle.write(PNG)
            self.assertTrue(find_sticker_file([base], md5).endswith(md5))
            self.assertEqual(find_sticker_file([base], "ff" + "c" * 30), "")

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
    def test_prepare_sticker_decrypts_a_cached_file(self):
        key = derive_key("12345", "wxid_test")
        md5 = "cd" + "e" * 30
        with tempfile.TemporaryDirectory() as root:
            base = os.path.join(root, "cache", "2026-10", "Emoticon", md5[:2])
            os.makedirs(base)
            with open(os.path.join(base, md5), "wb") as handle:
                handle.write(encrypt(PNG + b"\x00" * 64, key))
            out_dir = os.path.join(root, "out")
            path, mime = prepare_sticker([os.path.dirname(base)], md5, key, out_dir)
            self.assertTrue(path)
            self.assertEqual(mime, "image/png")
            with open(path, "rb") as handle:
                self.assertTrue(handle.read().startswith(b"\x89PNG"))

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
    def test_prepare_sticker_reuses_an_already_decoded_file(self):
        key = derive_key("12345", "wxid_test")
        md5 = "ef" + "f" * 30
        with tempfile.TemporaryDirectory() as root:
            out_dir = os.path.join(root, "out")
            os.makedirs(out_dir)
            cached = os.path.join(out_dir, f"{md5}.png")
            with open(cached, "wb") as handle:
                handle.write(PNG)
            path, mime = prepare_sticker([], md5, key, out_dir)
            self.assertEqual(path, cached)
            self.assertEqual(mime, "image/png")

    def test_prepare_sticker_reports_a_missing_file(self):
        path, mime = prepare_sticker([], "aa" + "b" * 30, b"0" * 16, tempfile.gettempdir())
        self.assertEqual(path, "")
        self.assertEqual(mime, "")


if __name__ == "__main__":
    unittest.main()
