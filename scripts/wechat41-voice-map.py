#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Voice media map for the weflow-cli (4.1 / encrypted DB) path.

The 4.2 bridge works on plaintext DBs. This script works on encrypted
`media_*.db` through weflow-cli's own `wechat_voice.py` helpers:

  wechat_voice.load_voice_map()  -> {local_id: SILK bytes}
  pysilk / pilk                 -> WAV

Output is the same media-map shape consumed by `--media-map`:

  { "items": { "<local_id>:<create_time>": { "kind": "voice", "media": ... } } }

Example (4.1):
  python scripts/wechat41-voice-map.py \
    --weflow-cli-dir "C:\\Program Files\\node...\\node_modules\\weflow-cli\\scripts" \
    --config "C:\\Users\\<你>\\.weflow-cli\\config.json" \
    --target "wxid_xxx" --from 2026-10-01 --to 2026-11-17 --out voice-map.json

4.2 sandbox can be tested the same way by pointing --config at the sandbox
home's `.weflow-cli/config.json` (all DBs share one raw key+salt).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import os
import re
import sys
import wave
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.voice_decode import decode_silk_to_wav  # noqa: E402
from lib.safety import assert_outside  # noqa: E402


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def local_timestamp(value: str):
    if not value:
        return None
    return int(datetime.strptime(value, "%Y-%m-%d").timestamp())


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="4.1/加密库语音媒体映射")
    ap.add_argument("--weflow-cli-dir", required=True, help="weflow-cli 的 scripts 目录")
    ap.add_argument("--config", required=True, help="weflow-cli config.json")
    ap.add_argument("--target", required=True, help="会话 username")
    ap.add_argument("--from", dest="since", default="")
    ap.add_argument("--to", dest="until", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    scripts_dir = Path(args.weflow_cli_dir).resolve()
    if not (scripts_dir / "wechat_voice.py").is_file():
        raise SystemExit(f"不是有效的 weflow-cli scripts 目录: {scripts_dir}")
    sys.path.insert(0, str(scripts_dir))
    import wechat_voice  # noqa: E402
    from export_chat_html import connect, derive_database_key  # noqa: E402

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    db_path = cfg.get("ntDbPath") or ""
    key_hex = cfg.get("ntKey") or cfg.get("decryptKey") or ""
    salt_hex = cfg.get("ntSalt") or ""
    passphrase = cfg.get("favPassphrase") or cfg.get("decryptKey") or ""
    if not db_path or not key_hex or not salt_hex:
        raise SystemExit("config 缺少 ntDbPath/ntKey/ntSalt")

    # Read-only contract: decoded WAVs and the map must stay outside the
    # WeChat account directory.
    account_root = str(Path(db_path).parent)
    for candidate in [Path(db_path), *Path(db_path).parents]:
        if (candidate / "db_storage").is_dir() or candidate.name == str(cfg.get("wxid") or ""):
            account_root = str(candidate)
            break
    safe = re.sub(r'[\\/:*?"<>|]', "_", args.target)[:80]
    out_dir = Path(args.out).resolve().parent / "_voices" / safe
    assert_outside(account_root, out_dir, "语音输出目录")
    assert_outside(account_root, Path(args.out).resolve(), "语音映射文件")

    since_ts = local_timestamp(args.since) if args.since else None
    until_ts = (local_timestamp(args.until) + 86399) if args.until else None

    # Message rows for this conversation: local_id -> create_time.
    # The talker's table can live in any message_*.db shard.
    table = "Msg_" + hashlib.md5(args.target.encode()).hexdigest()
    rows = []
    db_dir = Path(db_path).parent
    for shard in sorted(glob.glob(str(db_dir / "message_*.db"))):
        try:
            shard_key, shard_salt = derive_database_key(shard, key_hex, salt_hex, passphrase)
            conn, cur = connect(shard, shard_key, shard_salt)
            try:
                cur.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
                if not cur.fetchone():
                    continue
                cur.execute(f'SELECT local_id, create_time, local_type FROM "{table}"')
                for local_id, create_time, local_type in cur.fetchall():
                    base = int(local_type) & 0xFFFFFFFF
                    if base != 34:
                        continue
                    if since_ts is not None and create_time < since_ts:
                        continue
                    if until_ts is not None and create_time > until_ts:
                        continue
                    rows.append((local_id, create_time))
            finally:
                conn.close()
        except Exception:
            continue

    voices = wechat_voice.load_voice_map(
        db_path, key_hex, salt_hex, passphrase, args.target
    )
    items = {}
    ok = failed = 0
    for local_id, create_time in rows:
        blob = voices.get(local_id)
        if not blob:
            failed += 1
            continue
        stamp = datetime.fromtimestamp(create_time).strftime("%Y%m%d_%H%M%S")
        wav_path = str(out_dir / f"{stamp}_{local_id}.wav")
        if not os.path.exists(wav_path) and not decode_silk_to_wav(bytes(blob), wav_path):
            failed += 1
            continue
        ok += 1
        items[f"{local_id}:{create_time}"] = {
            "kind": "voice",
            "time": datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S"),
            "sender": "",
            "media": wav_path,
        }

    payload = {
        "schema": "wechat42-media-map/v1",
        "target": args.target,
        "range": {"since": args.since, "until": args.until},
        "counts": {"voice": len(rows), "voice_local": ok, "voice_failed": failed},
        "items": items,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "success": True,
        "target": args.target,
        "range": payload["range"],
        "counts": payload["counts"],
        "items": len(items),
        "out": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
