#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sticker (表情包) media map for one session/time range.

WeChat's custom stickers are separate messages (local_type 47). The message
XML carries the sticker's md5; the artwork itself lives in the account's
encrypted sticker cache. This script

  1. finds the type-47 rows of one conversation,
  2. reads the sticker md5 out of each row's `<emoji md5="...">`,
  3. decrypts the cached file (AES-128-CBC, seed-derived key) and decodes
     WeChat's `wxgf` H.265 container into PNG/GIF.

Output has the same shape as the voice/image media map, consumed by
`--media-map`:

  { "items": { "<local_id>:<create_time>": { "kind": "sticker", "media": ... } } }

Two database modes:
  * 4.2 - `--work-dir` points at the decrypted DBs (plain sqlite3).
  * 4.1 - `--weflow-cli-dir` + `--config` read the encrypted DB through
    weflow-cli's own helpers.

The sticker seed is needed to decrypt. It comes from `--seed`, from
`emoticonSeed` in the weflow config, or from a memory scan of the running
WeChat client (see scripts/wechat-sticker-seed.py).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import zstandard
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.sticker import (  # noqa: E402
    account_wxid,
    derive_key,
    find_seed,
    prepare_sticker,
    sticker_cache_dirs,
)
from lib.safety import assert_outside  # noqa: E402

#: `<emoji ... md5="0123...">` - the id of the cached artwork.
EMOJI_TAG = re.compile(r"<emoji\b[^>]*>", re.IGNORECASE)
MD5_ATTR = re.compile(r'\bmd5="([0-9a-fA-F]{32})"')
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def decode_content(blob) -> str:
    """Message content: zstd-compressed (4.2) or plain text (4.1)."""
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob
    raw = bytes(blob)
    if raw.startswith(ZSTD_MAGIC):
        try:
            raw = zstandard.ZstdDecompressor().decompress(raw)
        except Exception:
            return ""
    return raw.decode("utf-8", "replace")


def sticker_md5(content: str) -> str:
    """The sticker id from a type-47 message's XML."""
    text = content or ""
    if "<emoji" not in text.lower():
        text = html.unescape(text)
    match = EMOJI_TAG.search(text)
    if not match:
        return ""
    md5 = MD5_ATTR.search(match.group(0))
    return md5.group(1).lower() if md5 else ""


def rows_from_plain(work: Path, target: str):
    """(local_id, create_time, content) for type-47 rows of `target`."""
    table = "Msg_" + hashlib.md5(target.encode()).hexdigest()
    out = []
    for shard in sorted(work.glob("message/message_*.db")):
        try:
            conn = sqlite3.connect(str(shard))
        except sqlite3.Error:
            continue
        try:
            names = {row[0] for row in conn.execute(
                "select name from sqlite_master where type='table'")}
            if table not in names:
                continue
            for local_id, create_time, local_type, content in conn.execute(
                    f'select local_id, create_time, local_type, message_content from "{table}"'):
                if int(local_type) & 0xFFFFFFFF != 47:
                    continue
                out.append((local_id, create_time, decode_content(content)))
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return out


def rows_from_encrypted(scripts_dir: Path, config_path: Path, target: str):
    """Same, but through weflow-cli's encrypted-DB helpers (4.1 path)."""
    sys.path.insert(0, str(scripts_dir))
    import wechat_voice  # noqa: F401,E402
    from export_chat_html import connect, derive_database_key  # noqa: E402

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db_path = cfg.get("ntDbPath") or ""
    key_hex = cfg.get("ntKey") or cfg.get("decryptKey") or ""
    salt_hex = cfg.get("ntSalt") or ""
    passphrase = cfg.get("favPassphrase") or cfg.get("decryptKey") or ""
    if not db_path or not key_hex or not salt_hex:
        raise SystemExit("config 缺少 ntDbPath/ntKey/ntSalt")

    table = "Msg_" + hashlib.md5(target.encode()).hexdigest()
    out = []
    for shard in sorted(Path(db_path).parent.glob("message_*.db")):
        try:
            shard_key, shard_salt = derive_database_key(str(shard), key_hex, salt_hex, passphrase)
            conn, cur = connect(str(shard), shard_key, shard_salt)
        except Exception:
            continue
        try:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                continue
            cur.execute(f'SELECT local_id, create_time, local_type, message_content FROM "{table}"')
            for local_id, create_time, local_type, content in cur.fetchall():
                if int(local_type) & 0xFFFFFFFF != 47:
                    continue
                out.append((local_id, create_time, decode_content(content)))
        except Exception:
            continue
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return out


def account_root_from_config(config_path: Path) -> str:
    """The account directory for a weflow config (4.1 path)."""
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db_path = cfg.get("ntDbPath") or ""
    wxid = cfg.get("wxid") or ""
    if db_path and wxid:
        candidate = Path(db_path).parent / wxid
        if candidate.is_dir():
            return str(candidate)
        if Path(db_path).name == wxid:
            return db_path
    if db_path and Path(db_path).is_dir():
        return db_path
    return ""


def find_cache_dirs(account_root: str):
    """Sticker cache directories, plus a broad fallback for other layouts."""
    dirs = sticker_cache_dirs(account_root)
    if dirs:
        return dirs
    root = Path(account_root)
    if not root.is_dir():
        return []
    found = []
    for dirpath, dirnames, _files in os.walk(root):
        for name in dirnames:
            if name.lower() == "emoticon":
                found.append(dirpath)
        if len(found) > 40:
            break
    return found


def resolve_seed(seed_arg: str, config_path: str, wxid: str, sample: str) -> str:
    if seed_arg:
        return seed_arg.strip()
    if config_path and Path(config_path).is_file():
        try:
            cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
            configured = str(cfg.get("emoticonSeed") or "").strip()
        except (OSError, ValueError):
            configured = ""
        if configured:
            found = find_seed(wxid, sample, candidates=[configured]) if sample else None
            if found:
                return found
            print("! 配置里的 emoticonSeed 解不开样本；忽略", file=sys.stderr)
    if sample:
        found = find_seed(wxid, sample)
        if found:
            return found
    return ""


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="生成表情包媒体映射")
    ap.add_argument("--work-dir", default="", help="4.2：已解密的工作目录")
    ap.add_argument("--weflow-cli-dir", default="", help="4.1：weflow-cli scripts 目录")
    ap.add_argument("--config", default="", help="4.1：weflow-cli config.json")
    ap.add_argument("--account-root", default="", help="账号目录（含 cache/business）")
    ap.add_argument("--target", required=True, help="会话 username")
    ap.add_argument("--from", dest="since", default="")
    ap.add_argument("--to", dest="until", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", default="", help="表情包解密种子")
    ap.add_argument("--out-dir", default="", help="解码后的图片目录（缺省用 <out> 同级 _stickers）")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    if args.work_dir:
        work = Path(args.work_dir).resolve()
        rows = rows_from_plain(work, args.target)
    elif args.weflow_cli_dir and args.config:
        rows = rows_from_encrypted(
            Path(args.weflow_cli_dir).resolve(), Path(args.config).resolve(), args.target)
    else:
        raise SystemExit("需要 --work-dir 或 --weflow-cli-dir + --config")

    account_root = args.account_root
    if not account_root and args.config:
        account_root = account_root_from_config(Path(args.config).resolve())
    if not account_root:
        raise SystemExit("需要 --account-root（或在 config 里提供账号目录）")

    cache_dirs = find_cache_dirs(account_root)
    if not cache_dirs:
        print("! 没有找到表情缓存目录；表情将只保留文字标签", file=sys.stderr)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else \
        Path(args.out).resolve().parent / "_stickers"
    # Read-only contract: decoded stickers must never be written into the
    # WeChat account directory.
    assert_outside(account_root, out_dir, "表情输出目录")
    assert_outside(account_root, Path(args.out).resolve(), "表情映射文件")
    out_dir.mkdir(parents=True, exist_ok=True)

    wxid = account_wxid(Path(account_root).name)
    sample = ""
    for base in cache_dirs:
        for sub in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            candidate = os.path.join(base, sub)
            if not os.path.isdir(candidate):
                continue
            for name in sorted(os.listdir(candidate)):
                path = os.path.join(candidate, name)
                if os.path.isfile(path) and os.path.getsize(path) > 32:
                    sample = path
                    break
            if sample:
                break
        if sample:
            break

    seed = resolve_seed(args.seed, args.config, wxid, sample)
    key = derive_key(seed, wxid) if seed else b""

    since = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp()) if args.since else None
    until = (int(datetime.strptime(args.until, "%Y-%m-%d").timestamp()) + 86399) if args.until else None

    selected = []
    for local_id, create_time, content in rows:
        if since is not None and create_time < since:
            continue
        if until is not None and create_time > until:
            continue
        md5 = sticker_md5(content)
        if not md5:
            continue
        selected.append((local_id, create_time, md5))

    counts = {"sticker": len(selected), "sticker_local": 0, "sticker_missing": 0, "sticker_failed": 0}

    def decode_one(entry):
        local_id, create_time, md5 = entry
        if not key:
            return entry, "", "no-seed"
        path, _mime = prepare_sticker(cache_dirs, md5, key, str(out_dir))
        if not path:
            return entry, "", "missing"
        return entry, path, ""

    items = {}
    if selected:
        workers = max(1, min(int(args.workers or 4), 8))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for (local_id, create_time, md5), path, problem in pool.map(decode_one, selected):
                key_str = f"{local_id}:{create_time}"
                if path:
                    items[key_str] = {
                        "kind": "sticker",
                        "time": datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S"),
                        "sender": "",
                        "media": path,
                        "sticker_md5": md5,
                    }
                    counts["sticker_local"] += 1
                elif problem == "missing":
                    counts["sticker_missing"] += 1
                else:
                    counts["sticker_failed"] += 1

    payload = {
        "schema": "wechat42-media-map/v1",
        "target": args.target,
        "range": {"since": args.since, "until": args.until},
        "counts": counts,
        "items": items,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "success": True,
        "target": args.target,
        "range": payload["range"],
        "counts": counts,
        "seed": bool(seed),
        "items": len(items),
        "out": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
