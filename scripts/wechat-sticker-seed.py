#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve the account's sticker seed (表情包解密种子).

WeChat encrypts cached custom stickers with a per-account key derived from a
seed that only exists in the client's process memory. Two sources work:

  1. `weflow-cli config set emoticonSeed <value>` - explicit and stable.
  2. A memory scan of the running WeChat client (needs WeChat to be open).

Every candidate is proven by decrypting a real cached sticker and checking
the result for a known image magic, so a wrong seed cannot slip through.

Usage:
  python scripts/wechat-sticker-seed.py --account-root "<xwechat_files>/<账号目录>"
  python scripts/wechat-sticker-seed.py --account-root "..." --seed 123456789
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.sticker import (  # noqa: E402
    account_wxid,
    any_sticker_file,
    find_seed,
    sticker_cache_dirs,
)


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def seed_from_config(path: str) -> str:
    """`emoticonSeed` from a weflow-cli config.json (plaintext on disk)."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(cfg.get("emoticonSeed") or "").strip()


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="解析微信表情包解密种子")
    ap.add_argument("--account-root", required=True, help="账号目录（含 cache/business）")
    ap.add_argument("--config", default="", help="可选：weflow-cli config.json，用于读 emoticonSeed")
    ap.add_argument("--seed", default="", help="可选：直接指定种子")
    ap.add_argument("--out", default="", help="可选：把结果写到 JSON 文件")
    args = ap.parse_args()

    account = Path(args.account_root).resolve()
    dirs = sticker_cache_dirs(str(account))
    wxid = account_wxid(account.name)
    sample = any_sticker_file(dirs)

    source = ""
    seed = ""
    if args.seed:
        seed, source = args.seed.strip(), "argument"
    if not seed:
        configured = seed_from_config(args.config)
        if configured:
            found = find_seed(wxid, sample, candidates=[configured]) if sample else None
            if found:
                seed, source = found, "config"
            else:
                print("! 配置里的 emoticonSeed 解不开样本；忽略", file=sys.stderr)
    if not seed:
        if not sample:
            print("! 账号目录下没有表情缓存；先在微信里打开几个表情包", file=sys.stderr)
        else:
            found = find_seed(wxid, sample)
            if found:
                seed, source = found, "memory"

    payload = {
        "success": bool(seed),
        "seed": seed,
        "source": source,
        "wxid": wxid,
        "sample": sample,
        "cacheDirs": dirs,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if seed else 1


if __name__ == "__main__":
    raise SystemExit(main())
