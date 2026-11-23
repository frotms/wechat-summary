#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decrypt one WeChat image `.dat`/V2 file.

This is a debugging helper for the export pipeline. It accepts an explicit
AES key + XOR byte, or derives them from the current account's
`msg/attach` samples when `--account-root` is given (requires the WeChat
`kvcomm/key_*_*.statistic` files in APPDATA; no WeChat login is needed).

Output must be outside the WeChat account directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.safety import assert_outside  # noqa: E402
from lib.v2_decode import clean_account_id, find_codes  # noqa: E402
from vendor.wechat42.media import decode_dat, derive_image_key  # noqa: E402


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def detect_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"wxgf":
        return ".wxgf"
    return ".bin"


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="解密单个微信图片 .dat")
    ap.add_argument("input", help="输入 .dat/.wxgf 文件")
    ap.add_argument("--output", required=True, help="输出文件或目录")
    ap.add_argument("--aes", default="", help="16 位 ASCII 图片 key")
    ap.add_argument("--xor", type=lambda v: int(v, 0), default=None, help="XOR 字节（0-255）")
    ap.add_argument("--account-root", default="", help="从该账号的 attach 样本现场派生 key")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    src = Path(args.input).resolve()
    if not src.is_file():
        print(json.dumps({"success": False, "code": "INPUT_MISSING",
                          "error": f"文件不存在: {src}"}, ensure_ascii=False))
        return 2

    aes_key = args.aes
    xor_key = 0 if args.xor is None else args.xor
    source = "arguments"
    if not aes_key and args.account_root:
        account = Path(args.account_root).resolve()
        attach = account / "msg" / "attach"
        derived = derive_image_key(
            str(attach), clean_account_id(account.name), find_codes())
        if derived:
            _code, aes_key, xor_key = derived
            source = "account"
    if not aes_key:
        print(json.dumps({
            "success": False,
            "code": "IMAGE_KEY_MISSING",
            "error": "缺少图片 key",
            "hint": "提供 --aes/--xor，或用 --account-root 从本机 attach 样本派生",
        }, ensure_ascii=False))
        return 3
    if len(aes_key) != 16:
        print(json.dumps({"success": False, "code": "IMAGE_KEY_INVALID",
                          "error": "--aes 必须是 16 位 ASCII"}, ensure_ascii=False))
        return 3

    data = decode_dat(str(src), aes_key, xor_key)
    ext = detect_ext(data)
    out = Path(args.output).resolve()
    if out.is_dir() or str(args.output).endswith(("/", "\\")):
        out = out / f"{src.stem}{ext}"
    if args.account_root:
        assert_outside(Path(args.account_root).resolve(), out, "解密图片输出")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)

    summary = {
        "success": True,
        "input": str(src),
        "output": str(out),
        "bytes": len(data),
        "format": ext.lstrip("."),
        "keySource": source,
        "xor": xor_key,
    }
    if ext == ".wxgf":
        summary["warning"] = "wxgf/H.265 需要 ffmpeg 转码；当前只保存原始容器"
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"输出: {out} ({len(data)} bytes, {ext})")
        if summary.get("warning"):
            print(summary["warning"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
