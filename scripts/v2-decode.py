#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decode WeChat V2-encrypted videos into playable MP4.

Usage:
  python scripts/v2-decode.py \
    --video-dir "<account>/msg/video" \
    --account-root "<account>" \
    --out-dir "<work>/video"

Then pass `--video-root "<work>/video"` to wechat-export.mjs so the video
index picks the decoded files.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.v2_decode import (  # noqa: E402
    MAGIC, clean_account_id, decode_v2_file, find_codes,
)


def main():
    ap = argparse.ArgumentParser(description="解码微信 V2 加密视频")
    ap.add_argument("--video-dir", required=True, help="账号的 msg/video 目录")
    ap.add_argument("--account-root", default="", help="账号目录（用于推导 wxid）")
    ap.add_argument("--wxid", default="", help="账号 wxid；缺省从 account-root 目录名推导")
    ap.add_argument("--out-dir", required=True, help="解码后的 mp4 输出目录")
    ap.add_argument("--appdata", default="", help="kvcomm 所在的 AppData（默认环境变量）")
    ap.add_argument("--limit", type=int, default=0, help="最多检查多少个文件（0=全部）")
    args = ap.parse_args()

    video_dir = Path(args.video_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    if not video_dir.is_dir():
        raise SystemExit(f"视频目录不存在: {video_dir}")
    wxid = args.wxid or clean_account_id(Path(args.account_root).name)
    if not wxid:
        raise SystemExit("需要 --wxid 或 --account-root")
    codes = find_codes(args.appdata)
    if not codes:
        raise SystemExit("没有找到 kvcomm code；确认微信 4.x 的 kvcomm 目录存在")

    files = sorted(
        p for p in video_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".m4v")
    )
    if args.limit > 0:
        files = files[: args.limit]
    result = {"total": len(files), "v2": 0, "decoded": 0, "failed": 0, "examples": []}
    for path in files:
        try:
            with open(path, "rb") as handle:
                head = handle.read(6)
        except OSError:
            continue
        if head != MAGIC:
            continue
        result["v2"] += 1
        decoded = decode_v2_file(str(path), str(out_dir), wxid, codes)
        if decoded:
            result["decoded"] += 1
            if len(result["examples"]) < 5:
                result["examples"].append(str(decoded))
        else:
            result["failed"] += 1
    result["success"] = result["decoded"] == result["v2"]
    result["wxid"] = wxid
    result["codes"] = len(codes)
    result["out_dir"] = str(out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
