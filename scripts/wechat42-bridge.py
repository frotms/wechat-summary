#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WeChat 4.2 bridge for wechat-export.

This script intentionally does NOT reimplement key extraction. It imports the
MIT-licensed `wechat-exporter` project (TangGod991021/wechat-exporter):
  - app.core.keys.extract_keys    (Config.Cipher / plaintext scan)
  - app.core.decrypt.decrypt_all  (SQLCipher 4 page decrypt)
  - app.core.export.ChatStore     (read decrypted DBs, parse messages)

The user must explicitly name/authorize that upstream project. The bridge only
reads the user's own local WeChat data and writes a local export.

Example:
  python scripts/wechat42-bridge.py \
    --tool-dir "C:\\path\\to\\wechat-exporter" \
    --data-root "C:\\...\\xwechat_files\\<account>\\db_storage" \
    --work-dir "C:\\...\\wechat42-work" \
    --list

  python scripts/wechat42-bridge.py ... \
    --target "算能易科" --from 2026-11-16 --to 2026-11-16 \
    --out-dir "C:\\...\\exports\\算能易科_2026-11-16"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def ensure_utf8_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def import_tool(tool_dir: str = ""):
    if tool_dir:
        tool = Path(tool_dir).resolve()
        if (tool / "app" / "core" / "keys.py").is_file():
            sys.path.insert(0, str(tool))
            from app.core.keys import extract_keys  # noqa: E402
            from app.core.decrypt import chat_db_rels, decrypt_all  # noqa: E402
            from app.core.export import ChatStore  # noqa: E402
            return extract_keys, decrypt_all, chat_db_rels, ChatStore
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vendor.wechat42.keys import extract_keys  # noqa: E402
    from vendor.wechat42.decrypt import chat_db_rels, decrypt_all  # noqa: E402
    from vendor.wechat42.export import ChatStore  # noqa: E402
    return extract_keys, decrypt_all, chat_db_rels, ChatStore


def session_rows(store):
    rows = []
    for item in store.sessions():
        rows.append({
            "name": item.get("name") or item.get("username") or "",
            "username": item.get("username") or "",
            "is_group": bool(item.get("is_group")),
            "last_time": item.get("last_time_text") or "",
        })
    return rows


def resolve_target(rows, target: str):
    exact_id = [row for row in rows if row["username"] == target]
    if len(exact_id) == 1:
        return exact_id[0], []
    exact_name = [row for row in rows if row["name"] == target]
    if len(exact_name) == 1:
        return exact_name[0], []
    lower = target.lower()
    ci = [row for row in rows if row["name"].lower() == lower or row["username"].lower() == lower]
    if len(ci) == 1:
        return ci[0], []
    contains = [row for row in rows if lower in row["name"].lower() or lower in row["username"].lower()]
    if len(contains) == 1:
        return contains[0], []
    return None, contains or exact_name or exact_id


def write_outputs(store, target_row, msgs, out_dir: Path, since: str, until: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    compact = []
    for m in msgs:
        compact.append({
            "time": m.get("time", ""),
            "ts": m.get("ts"),
            "sender": m.get("sender", ""),
            "sender_wxid": m.get("sender_wxid", ""),
            "type": m.get("type", ""),
            "kind": m.get("kind", ""),
            "text": m.get("text", ""),
            "media": m.get("media", ""),
            "poster": m.get("poster", ""),
        })
    payload = {
        "schema": "wechat42-bridge/v1",
        "target": target_row,
        "range": {"since": since, "until": until},
        "count": len(compact),
        "messages": compact,
    }
    json_path = out_dir / "chat.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"{target_row['name']} 聊天记录 {since} ~ {until}（{len(compact)} 条）", ""]
    for m in compact:
        hhmmss = (m.get("time") or "")[11:19]
        lines.append(f"[{hhmmss}] {m.get('sender') or '?'}: {m.get('text') or ''}")
    txt_path = out_dir / "chat.txt"
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload, json_path, txt_path


def main():
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser(description="微信 4.2 桥接（调用 wechat-exporter）")
    ap.add_argument("--tool-dir", default="", help="可选：外部 wechat-exporter 源码目录；缺省用本地 vendor")
    ap.add_argument("--data-root", required=True, help="账号的 db_storage 目录")
    ap.add_argument("--work-dir", required=True, help="解密后的数据库工作目录")
    ap.add_argument("--list", action="store_true", help="只列出会话")
    ap.add_argument("--target", help="会话名称或 ID")
    ap.add_argument("--from", dest="since", default="", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="until", default="", help="结束日期 YYYY-MM-DD（含当天）")
    ap.add_argument("--out-dir", default="", help="导出目录（chat.json / chat.txt）")
    ap.add_argument("--only-core", action="store_true", help="只解密 session/contact，不导消息")
    ap.add_argument("--clean-work", action="store_true", help="完成后删除解密工作目录")
    args = ap.parse_args()

    extract_keys, decrypt_all, chat_db_rels, ChatStore = import_tool(args.tool_dir)
    data_root = Path(args.data_root).resolve()
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)

    keys, meta = extract_keys(str(data_root))
    if not keys:
        print(json.dumps({"success": False, "code": "KEY_NOT_FOUND", "meta": meta}, ensure_ascii=False))
        return 2

    rels = chat_db_rels(str(data_root))
    if args.only_core:
        rels = ["session/session.db", "contact/contact.db"]
    result = decrypt_all(
        str(data_root), str(work), keys, only_rels=rels,
        progress=lambda p: print(f"  decrypt {p}%", file=sys.stderr, flush=True),
    )

    account_root = data_root.parent
    store = ChatStore(
        str(work),
        images_dir=str(work / "_images"),
        voices_dir=str(work / "_voices"),
        my_wxid="",
        video_roots=[
            str(account_root / "msg" / "video"),
            str(account_root / "msg" / "attach"),
        ],
        video_cache=str(work / "video_index.json"),
    )
    info = store.self_info()
    store.my_wxid = info.get("wxid") or ""

    rows = session_rows(store)
    if args.list or not args.target:
        print(json.dumps({
            "success": True,
            "keys": {"total": len(keys), "mode": meta.get("mode")},
            "decrypt": {"ok": result.get("ok"), "failed": result.get("failed"), "total": result.get("total")},
            "count": len(rows),
            "sessions": rows,
        }, ensure_ascii=False, indent=2))
        return 0

    target_row, candidates = resolve_target(rows, args.target)
    if not target_row:
        print(json.dumps({
            "success": False,
            "code": "TARGET_NOT_FOUND_OR_AMBIGUOUS",
            "target": args.target,
            "candidates": candidates[:20],
        }, ensure_ascii=False, indent=2))
        return 3

    since = args.since
    until = args.until
    msgs = store.messages(target_row["username"], since=since or None, until=until or None)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (work / "export")
    payload, json_path, txt_path = write_outputs(store, target_row, msgs, out_dir, since, until)

    if args.clean_work:
        shutil.rmtree(work, ignore_errors=True)

    print(json.dumps({
        "success": True,
        "keys": {"total": len(keys), "mode": meta.get("mode")},
        "decrypt": {"ok": result.get("ok"), "failed": result.get("failed"), "total": result.get("total")},
        "target": target_row,
        "range": {"since": since, "until": until},
        "count": payload["count"],
        "json": str(json_path),
        "txt": str(txt_path),
        "preview": payload["messages"][:20],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
