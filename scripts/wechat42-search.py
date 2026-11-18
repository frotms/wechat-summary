#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Search decrypted WeChat 4.2 databases for keywords.

Uses the MIT-licensed `wechat-exporter` ChatStore against the **already
decrypted** work directory created by wechat42-bridge.py. It never touches
WeChat's original databases and never extracts keys.

Example:
  python scripts/wechat42-search.py --tool-dir <wechat-exporter> \
    --work-dir <decrypted-work> --query 1684X \
    --from 2026-08-17 --to 2026-11-17 --out search.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def import_store(tool_dir: str = ""):
    if tool_dir:
        tool = Path(tool_dir).resolve()
        if (tool / "app" / "core" / "export.py").is_file():
            sys.path.insert(0, str(tool))
            from app.core.export import ChatStore  # noqa: E402
            return ChatStore
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vendor.wechat42.export import ChatStore  # noqa: E402
    return ChatStore


def parse_day(text: str):
    if not text:
        return None
    return int(datetime.strptime(text, "%Y-%m-%d").timestamp())


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="在已解密的微信 4.2 库中搜索关键词")
    ap.add_argument("--tool-dir", default="", help="可选：外部 wechat-exporter 源码目录；缺省用本地 vendor")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--from", dest="since", default="")
    ap.add_argument("--to", dest="until", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--max-sessions", type=int, default=800)
    ap.add_argument("--max-matches", type=int, default=500)
    args = ap.parse_args()

    ChatStore = import_store(args.tool_dir)
    work = Path(args.work_dir).resolve()
    store = ChatStore(str(work), images_dir=str(work / "_images"), voices_dir=str(work / "_voices"))
    info = store.self_info()
    store.my_wxid = info.get("wxid") or ""

    since_ts = parse_day(args.since) if args.since else None
    until_ts = (parse_day(args.until) + 86399) if args.until else None
    needle = args.query.lower()

    sessions = store.sessions()
    sessions.sort(key=lambda s: int(s.get("last_time") or 0), reverse=True)
    if since_ts:
        sessions = [s for s in sessions if int(s.get("last_time") or 0) >= since_ts]
    sessions = sessions[: args.max_sessions]

    matches = []
    scanned = 0
    for index, session in enumerate(sessions, 1):
        username = session.get("username") or ""
        name = session.get("name") or username
        if not username:
            continue
        if needle in name.lower() or needle in username.lower():
            matches.append({
                "session": name,
                "username": username,
                "is_group": bool(session.get("is_group")),
                "time": session.get("last_time_text") or "",
                "sender": "",
                "text": "[会话名称/ID 命中]",
                "media": "",
            })
        try:
            msgs = store.messages(username, since=args.since or None, until=args.until or None)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {name}: {exc}", file=sys.stderr)
            continue
        scanned += 1
        for msg in msgs:
            blob = (str(msg.get("text") or "") + "\n" + json.dumps(
                msg.get("content") or {}, ensure_ascii=False)).lower()
            if needle not in blob:
                continue
            matches.append({
                "session": name,
                "username": username,
                "is_group": bool(session.get("is_group")),
                "time": msg.get("time") or "",
                "sender": msg.get("sender") or "",
                "text": msg.get("text") or "",
                "media": msg.get("media") or "",
            })
            if len(matches) >= args.max_matches:
                break
        if len(matches) >= args.max_matches:
            break
        if index % 50 == 0:
            print(f"  scanned {index}/{len(sessions)} sessions, matches {len(matches)}",
                  file=sys.stderr, flush=True)

    payload = {
        "schema": "wechat42-search/v1",
        "query": args.query,
        "range": {"since": args.since, "until": args.until},
        "scanned_sessions": scanned,
        "count": len(matches),
        "matches": matches,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "query": args.query,
        "range": payload["range"],
        "scanned_sessions": scanned,
        "count": len(matches),
        "out": args.out or None,
        "preview": matches[:30],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
