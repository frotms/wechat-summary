#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read WeChat's revoke traces for one conversation.

WeChat keeps two trace tables in `general/general.db`:

  revokemessage(to_user_name, svr_id, message_type, revoke_time, content, at_user_list)
  revokebatchmessage(local_id, batch_id, msg_unique_id, session_name,
                     msg_local_id, msg_create_time)

A normal single revoke usually leaves only a `sysmsg type="revokemsg"` notice in
the message table, with no pointer to the original row. The trace tables are the
places where the original can still be identified:

  * batch: `msg_local_id` + `msg_create_time` point at the original message row;
  * single: `svr_id` can be matched against the message table's `server_id`,
    and `content` may carry the original content.

Output (consumed by `--revoke-map`):

  {
    "items": {
      "<local_id>:<create_time>": {
        "revoked": true, "kind": "batch|single", "batch_id": ...,
        "msg_unique_id": ..., "revoke_time": ..., "content": ...,
        "server_id": ...
      }
    },
    "orphans": [ { ... single row with no local message ... } ]
  }

Read-only: the database is opened read-only and nothing is written to WeChat.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def table_columns(conn, table: str):
    try:
        return [row[1] for row in conn.execute(f'pragma table_info("{table}")')]
    except sqlite3.Error:
        return []


def select_rows(conn, table: str, wanted: list[str]):
    cols = table_columns(conn, table)
    if not cols:
        return []
    present = [name for name in wanted if name in cols]
    if not present:
        return []
    sql = f'SELECT {", ".join(present)} FROM "{table}"'
    try:
        return [dict(zip(present, row)) for row in conn.execute(sql)]
    except sqlite3.Error:
        return []


def window(since: str, until: str):
    start = int(datetime.strptime(since, "%Y-%m-%d").timestamp()) if since else None
    end = (int(datetime.strptime(until, "%Y-%m-%d").timestamp()) + 86399) if until else None
    return start, end


def in_window(value, start, end):
    if value is None:
        return True
    try:
        number = int(value)
    except (TypeError, ValueError):
        return True
    # Some builds store milliseconds; normalise for the window check.
    if number > 10 ** 12:
        number //= 1000
    if start is not None and number < start:
        return False
    if end is not None and number > end:
        return False
    return True


# --------------------------------------------------------------------------
# database access
# --------------------------------------------------------------------------

def plain_general(work: Path):
    path = work / "general" / "general.db"
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True) if path.is_file() else None


def message_rows_plain(work: Path, target: str):
    table = "Msg_" + hashlib.md5(target.encode()).hexdigest()
    rows = []
    for shard in sorted(work.glob("message/message_*.db")):
        try:
            conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            names = {row[0] for row in conn.execute(
                "select name from sqlite_master where type='table'")}
            if table not in names:
                continue
            for local_id, server_id, create_time, local_type in conn.execute(
                    f'select local_id, server_id, create_time, local_type from "{table}"'):
                rows.append({
                    "local_id": int(local_id),
                    "server_id": str(server_id or ""),
                    "create_time": int(create_time),
                    "local_type": int(local_type),
                })
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return rows


def find_encrypted_general(db_path: Path):
    candidates = []
    if db_path.is_file():
        candidates.append(db_path.parent.parent / "general" / "general.db")
    if db_path.is_dir():
        candidates.append(db_path / "general" / "general.db")
    candidates.append(db_path.parent / "general" / "general.db")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Last resort: look next to the message directory.
    for candidate in glob.glob(str(db_path.parent.parent / "**" / "general.db"), recursive=True):
        if os.path.basename(os.path.dirname(candidate)) == "general":
            return Path(candidate)
    return None


def encrypted_connection(scripts_dir: Path, config_path: Path):
    sys.path.insert(0, str(scripts_dir))
    from export_chat_html import connect, derive_database_key  # noqa: E402

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db_path = cfg.get("ntDbPath") or cfg.get("dbPath") or ""
    key_hex = cfg.get("ntKey") or cfg.get("decryptKey") or ""
    salt_hex = cfg.get("ntSalt") or ""
    passphrase = cfg.get("favPassphrase") or cfg.get("decryptKey") or ""
    if not db_path or not key_hex or not salt_hex:
        raise SystemExit("config 缺少 ntDbPath/dbPath + ntKey/ntSalt")

    general = find_encrypted_general(Path(db_path))
    if not general:
        return None, Path(db_path)
    key, salt = derive_database_key(str(general), key_hex, salt_hex, passphrase)
    conn, _cur = connect(str(general), key, salt)
    return conn, Path(db_path)


def message_rows_encrypted(scripts_dir: Path, config_path: Path, target: str):
    sys.path.insert(0, str(scripts_dir))
    from export_chat_html import connect, derive_database_key  # noqa: E402

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db_path = Path(cfg.get("ntDbPath") or cfg.get("dbPath") or "")
    key_hex = cfg.get("ntKey") or cfg.get("decryptKey") or ""
    salt_hex = cfg.get("ntSalt") or ""
    passphrase = cfg.get("favPassphrase") or cfg.get("decryptKey") or ""
    table = "Msg_" + hashlib.md5(target.encode()).hexdigest()
    rows = []
    base = db_path.parent if db_path.is_file() else db_path
    for shard in sorted(base.glob("message_*.db")):
        try:
            key, salt = derive_database_key(str(shard), key_hex, salt_hex, passphrase)
            conn, cur = connect(str(shard), key, salt)
        except Exception:
            continue
        try:
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                continue
            cur.execute(f'SELECT local_id, server_id, create_time, local_type FROM "{table}"')
            for local_id, server_id, create_time, local_type in cur.fetchall():
                rows.append({
                    "local_id": int(local_id),
                    "server_id": str(server_id or ""),
                    "create_time": int(create_time),
                    "local_type": int(local_type),
                })
        except Exception:
            continue
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return rows


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="生成撤回记录映射")
    ap.add_argument("--work-dir", default="", help="4.2：已解密的工作目录")
    ap.add_argument("--weflow-cli-dir", default="", help="4.1：weflow-cli scripts 目录")
    ap.add_argument("--config", default="", help="4.1：weflow-cli config.json")
    ap.add_argument("--target", required=True)
    ap.add_argument("--from", dest="since", default="")
    ap.add_argument("--to", dest="until", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start, end = window(args.since, args.until)

    conn = None
    message_rows = []
    if args.work_dir:
        work = Path(args.work_dir).resolve()
        conn = plain_general(work)
        message_rows = message_rows_plain(work, args.target)
    elif args.weflow_cli_dir and args.config:
        scripts = Path(args.weflow_cli_dir).resolve()
        config = Path(args.config).resolve()
        conn, _ = encrypted_connection(scripts, config)
        message_rows = message_rows_encrypted(scripts, config, args.target)
    else:
        raise SystemExit("需要 --work-dir 或 --weflow-cli-dir + --config")

    if conn is None:
        print("! 没有找到 general/general.db；撤回记录为空", file=sys.stderr)

    batch = []
    single = []
    if conn is not None:
        try:
            batch = select_rows(conn, "revokebatchmessage", [
                "local_id", "batch_id", "msg_unique_id", "session_name",
                "msg_local_id", "msg_create_time",
            ])
            single = select_rows(conn, "revokemessage", [
                "to_user_name", "svr_id", "message_type", "revoke_time",
                "content", "at_user_list",
            ])
        except sqlite3.Error as exc:
            print(f"! 读取撤回表失败: {exc}", file=sys.stderr)
        finally:
            conn.close()

    by_server = {}
    by_local = {}
    for row in message_rows:
        by_server.setdefault(row["server_id"], row)
        by_local[(row["local_id"], row["create_time"])] = row

    items = {}
    orphans = []
    for row in batch:
        if str(row.get("session_name") or "") != args.target:
            continue
        create_time = int(row.get("msg_create_time") or 0)
        if not in_window(create_time, start, end):
            continue
        local_id = int(row.get("msg_local_id") or 0)
        key = f"{local_id}:{create_time}"
        original = by_local.get((local_id, create_time))
        items[key] = {
            "revoked": True,
            "kind": "batch",
            "batch_id": row.get("batch_id"),
            "msg_unique_id": str(row.get("msg_unique_id") or ""),
            "revoke_time": None,
            "content": "",
            "server_id": original["server_id"] if original else "",
            "message_local_id": local_id,
            "message_create_time": create_time,
        }

    for row in single:
        if str(row.get("to_user_name") or "") != args.target:
            continue
        server_id = str(row.get("svr_id") or "")
        original = by_server.get(server_id)
        create_time = int(original["create_time"]) if original else int(row.get("revoke_time") or 0)
        if not in_window(create_time, start, end):
            continue
        entry = {
            "revoked": True,
            "kind": "single",
            "batch_id": None,
            "msg_unique_id": "",
            "revoke_time": int(row.get("revoke_time") or 0),
            "content": row.get("content") or "",
            "server_id": server_id,
            "message_local_id": original["local_id"] if original else None,
            "message_create_time": create_time or None,
            "target": args.target,
        }
        if original:
            items[f'{original["local_id"]}:{original["create_time"]}'] = entry
        else:
            orphans.append(entry)

    counts = {
        "batch": sum(1 for item in items.values() if item["kind"] == "batch"),
        "single": sum(1 for item in items.values() if item["kind"] == "single"),
        "matched": len(items),
        "orphans": len(orphans),
    }
    payload = {
        "schema": "wechat-revoke-map/v1",
        "target": args.target,
        "range": {"since": args.since, "until": args.until},
        "counts": counts,
        "items": items,
        "orphans": orphans,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "success": True,
        "target": args.target,
        "counts": counts,
        "out": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
