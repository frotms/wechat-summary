# -*- coding: utf-8 -*-
"""Tests for native-backend cache invalidation helpers."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "native_backend", ROOT / "scripts" / "native-backend.py")
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class NativeCacheTest(unittest.TestCase):
    def test_article_fetch_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            account = root / "account"
            account.mkdir()
            out = root / "out"
            msgs = [{
                "local_id": 1, "ts": 123, "kind": "text",
                "content": {},
                "raw_content": "看这个 https://mp.weixin.qq.com/s/x",
            }]
            with mock.patch.object(native, "fetch_article",
                                   return_value=("<html>x</html>", "标题", "正文")), \
                    mock.patch.dict(os.environ,
                                    {"WEFLOW_NATIVE_FETCH_ARTICLES": "1"}):
                native.write_assets(None, msgs, out, "t", account, work)
            manifest = json.loads(
                (out / "t_assets" / "manifest.json").read_text(encoding="utf-8"))
            entry = manifest["messages"]["1:123"]
            self.assertEqual(entry["kind"], "article")
            self.assertEqual(entry["title"], "标题")
            self.assertTrue((out / entry["path"]).is_file())
            self.assertTrue((out / entry["html"]).is_file())

    def test_sticker_download_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            account = root / "account"
            account.mkdir()
            out = root / "out"
            md5 = "a" * 32
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
            msgs = [{
                "local_id": 2, "ts": 456, "kind": "sticker",
                "raw_content": f'<emoji md5="{md5}" cdnurl="https://a.qpic.cn/x"/>',
            }]
            with mock.patch.object(native, "prepare_target_stickers", return_value={}), \
                    mock.patch.object(native, "fetch_sticker", return_value=(png, ".png")), \
                    mock.patch.dict(os.environ,
                                    {"WEFLOW_NATIVE_DOWNLOAD_STICKERS": "1"}):
                native.write_assets(None, msgs, out, "t", account, work)
            manifest = json.loads(
                (out / "t_assets" / "manifest.json").read_text(encoding="utf-8"))
            entry = manifest["messages"]["2:456"]
            self.assertEqual(entry["kind"], "sticker")
            self.assertTrue((out / entry["path"]).is_file())

    def test_file_index_ttl_and_force_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            account = root / "account"
            files = account / "msg" / "file" / "2026-01"
            files.mkdir(parents=True)
            (files / "a.pdf").write_text("a", encoding="utf-8")

            index = native.file_index(work, account)
            self.assertIn("a.pdf", index)

            (files / "b.pdf").write_text("b", encoding="utf-8")
            with mock.patch.dict(os.environ, {"WEFLOW_NATIVE_FILE_INDEX_TTL": "0"}):
                index = native.file_index(work, account)
            self.assertIn("b.pdf", index)

            (files / "c.pdf").write_text("c", encoding="utf-8")
            with mock.patch.dict(os.environ, {"WEFLOW_NATIVE_FILE_INDEX_TTL": "3600"}):
                cached = native.file_index(work, account)
            self.assertNotIn("c.pdf", cached)

            with mock.patch.dict(os.environ, {
                "WEFLOW_NATIVE_FILE_INDEX_TTL": "3600",
                "WEFLOW_NATIVE_REFRESH_FILE_INDEX": "1",
            }):
                refreshed = native.file_index(work, account)
            self.assertIn("c.pdf", refreshed)

    def test_stale_sticker_seed_is_redetected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            account = root / "account"
            account.mkdir()
            (work / "_native_sticker_seed").write_text("old", encoding="utf-8")
            md5 = "a" * 32
            msgs = [{"kind": "sticker", "raw_content": f'<emoji md5="{md5}"/>'}]

            def fake_prepare(_dirs, _md5, key, _out):
                return (("/tmp/sticker.png", "image/png") if key == b"new"
                        else (None, None))

            with mock.patch.object(native, "sticker_cache_dirs", return_value=["cache"]), \
                    mock.patch.object(native, "any_sticker_file", return_value="sample"), \
                    mock.patch.object(native, "derive_sticker_key",
                                      side_effect=lambda seed, _wxid: seed.encode()), \
                    mock.patch.object(native, "find_sticker_seed", return_value="new"), \
                    mock.patch.object(native, "prepare_sticker", side_effect=fake_prepare):
                result = native.prepare_target_stickers(work, account, msgs)

            self.assertEqual(result, {md5: "/tmp/sticker.png"})
            self.assertEqual(
                (work / "_native_sticker_seed").read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
