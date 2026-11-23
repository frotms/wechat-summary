# -*- coding: utf-8 -*-
"""Tests for allowlisted article/sticker helpers."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from lib import net  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class NetTest(unittest.TestCase):
    def test_host_allowlist(self):
        self.assertTrue(net.host_ok("https://mp.weixin.qq.com/s/x", exact="mp.weixin.qq.com"))
        self.assertFalse(net.host_ok("https://evil.example/x", exact="mp.weixin.qq.com"))
        self.assertTrue(net.host_ok("https://a.qpic.cn/x", suffixes=net.STICKER_HOST_SUFFIXES))
        self.assertFalse(net.host_ok("https://qpic.cn.evil/x", suffixes=net.STICKER_HOST_SUFFIXES))
        self.assertFalse(net.host_ok("https://user@mp.weixin.qq.com/x", exact="mp.weixin.qq.com"))
        self.assertFalse(net.host_ok("ftp://mp.weixin.qq.com/x", exact="mp.weixin.qq.com"))

    def test_article_title(self):
        page = '<meta property="og:title" content="标题 &amp; 副标题">'
        self.assertEqual(net.article_title(page), "标题 & 副标题")

    def test_sticker_fetch_decrypts_or_returns_image(self):
        raw = '<emoji md5="a" cdnurl="https://a.qpic.cn/x" aeskey="' + "00" * 16 + '"/>'
        with mock.patch.object(net, "fetch_bytes", return_value=(PNG, "image/png")):
            data, ext = net.fetch_sticker(raw)
        self.assertEqual(data, PNG)
        self.assertEqual(ext, ".png")


if __name__ == "__main__":
    unittest.main()
