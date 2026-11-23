#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-session person export / global timeline for WeChat 4.2.

Reads only the decrypted `work/` directory. Writes a single JSONL sorted by
time plus a small summary. `--incremental` uses `<out>/state.json` so repeated
runs with the same output directory only append newer messages.

This is intentionally a lightweight aggregate view; full per-session media
export remains in `wechat42-export.mjs`.
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


def sender_matches(msg: dict, needle: str) -> bool:
    if not needle:
        return True
    target = needle.lower()
    values = (
        msg.get("sender") or "",
        msg.get("sender_wxid") or "",
        msg.get("senderUsername") or "",
    )
    return any(target in str(value).lower() for value in values if value)


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, last_timestamp: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "wechat-export-state/v1",
        "lastTimestamp": int(last_timestamp),
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def record_of(session: dict, msg: dict, username: str):
    return {
        "timestamp": int(msg.get("ts") or 0),
        "time": msg.get("time") or "",
        "session": session.get("name") or username,
        "sessionId": username,
        "isGroup": bool(session.get("is_group")),
        "sender": msg.get("sender") or "",
        "senderUsername": msg.get("sender_wxid") or "",
        "type": msg.get("type") or msg.get("kind") or "",
        "localId": msg.get("local_id"),
        "serverId": msg.get("server_id") or "",
        "text": msg.get("text") or "",
        "media": msg.get("media") or "",
    }


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="跨会话按人导出 / 全局时间线（4.2 work）")
    ap.add_argument("--tool-dir", default="")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--sender", default="", help="按发送者显示名/wxid 过滤；留空为全局时间线")
    ap.add_argument("--from", dest="since", default="")
    ap.add_argument("--to", dest="until", default="")
    ap.add_argument("--max-sessions", type=int, default=800)
    ap.add_argument("--max-messages", type=int, default=5000)
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--state-file", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ChatStore = import_store(args.tool_dir)
    work = Path(args.work_dir).resolve()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state_file).resolve() if args.state_file else out / "state.json"
    state = load_state(state_path) if args.incremental else {}
    last_ts = int(state.get("lastTimestamp") or 0)

    store = ChatStore(str(work), images_dir=str(work / "_images"),
                      voices_dir=str(work / "_voices"))
    info = store.self_info()
    store.my_wxid = info.get("wxid") or ""
    sessions = store.sessions()
    sessions.sort(key=lambda item: int(item.get("last_time") or 0), reverse=True)
    since_ts = parse_day(args.since) if args.since else None
    if since_ts:
        sessions = [s for s in sessions if int(s.get("last_time") or 0) >= since_ts]
    sessions = sessions[:args.max_sessions]

    records = []
    scanned = 0
    for index, session in enumerate(sessions, 1):
        username = session.get("username") or ""
        if not username:
            continue
        try:
            messages = store.messages(username, since=args.since or None,
                                      until=args.until or None)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {session.get('name') or username}: {exc}", file=sys.stderr)
            continue
        scanned += 1
        for msg in messages:
            if not sender_matches(msg, args.sender):
                continue
            record = record_of(session, msg, username)
            if args.incremental and record["timestamp"] <= last_ts:
                continue
            records.append(record)
        if index % 50 == 0:
            print(f"  scanned {index}/{len(sessions)} sessions, {len(records)} rows",
                  file=sys.stderr)
        if len(records) >= args.max_messages:
            break

    records.sort(key=lambda item: (item["timestamp"], item["sessionId"],
                                   item.get("localId") or 0))
    if len(records) > args.max_messages:
        records = records[-args.max_messages:]

    name = "person" if args.sender else "timeline"
    target = out / f"{name}.jsonl"
    new_count = len(records)
    if args.incremental and target.is_file():
        existing = []
        try:
            for line in target.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    existing.append(json.loads(line))
        except (OSError, ValueError):
            existing = []
        merged = {}
        for row in existing + records:
            key = (row.get("sessionId"), row.get("timestamp"), row.get("localId"))
            merged[key] = row
        records = sorted(merged.values(),
                         key=lambda item: (item.get("timestamp") or 0,
                                           item.get("sessionId") or "",
                                           item.get("localId") or 0))
        if len(records) > args.max_messages:
            records = records[-args.max_messages:]

    max_ts = max((item["timestamp"] for item in records), default=last_ts)
    if max_ts > last_ts:
        save_state(state_path, max_ts)

    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {
        "success": True,
        "mode": name,
        "sender": args.sender,
        "output": str(target),
        "state": str(state_path),
        "sessionsScanned": scanned,
        "newMessages": new_count,
        "totalMessages": len(records),
        "firstTimestamp": records[0]["timestamp"] if records else None,
        "lastTimestamp": max_ts if records else last_ts,
        "incremental": bool(args.incremental),
    }
    (out / f"{name}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"{name}: {len(records)} 条，扫描 {scanned} 个会话")
        print(f"输出: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
