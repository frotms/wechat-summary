#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export contacts, group members and avatars from the decrypted work DB.

Reads only `work/contact/contact.db` and `work/head_image/head_image.db`.
Writes `contacts.json`, `groups.json` and (optionally) `avatars/` under the
requested output directory. The output must stay outside the WeChat account
directory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.safety import assert_outside  # noqa: E402


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def connect_ro(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def safe_name(value: str, fallback: str = "contact") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "")).strip(". ")
    return name[:120] or fallback


def contact_type(username: str) -> str:
    value = str(username or "")
    if value.endswith("@chatroom"):
        return "group"
    if value.endswith("@openim"):
        return "openim"
    if value.startswith("gh_"):
        return "official"
    return "person"


def avatar_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def display_name(row) -> str:
    remark, nickname, alias, username = row
    return remark or nickname or alias or username


def write_avatar(out_dir: Path, username: str, md5: str, data: bytes):
    ext = avatar_ext(data)
    stem = safe_name(username) or "contact"
    if md5:
        stem = f"{stem}_{str(md5)[:8]}"
    target = out_dir / f"{stem}{ext}"
    target.write_bytes(data)
    return target


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="导出微信联系人/群成员/头像")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--account-root", default="")
    ap.add_argument("--output", required=True)
    ap.add_argument("--no-avatars", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    work = Path(args.work_dir).resolve()
    out = Path(args.output).resolve()
    if args.account_root:
        assert_outside(Path(args.account_root).resolve(), out, "联系人导出目录")
    out.mkdir(parents=True, exist_ok=True)

    contact_db = work / "contact" / "contact.db"
    head_db = work / "head_image" / "head_image.db"
    if not contact_db.is_file():
        print(json.dumps({"success": False, "code": "CONTACT_DB_MISSING",
                          "error": f"缺少 {contact_db}"}, ensure_ascii=False))
        return 3

    conn = connect_ro(contact_db)
    try:
        rows = conn.execute(
            "select id, username, local_type, alias, remark, nick_name, "
            "big_head_url, small_head_url, head_img_md5, delete_flag, description "
            "from contact order by username").fetchall()
        room_rows = conn.execute(
            "select cr.id, cr.username, cr.owner, c.remark, c.nick_name, c.alias "
            "from chat_room cr left join contact c on c.id = cr.id "
            "order by cr.username").fetchall()
        member_rows = conn.execute(
            "select cm.room_id, c.username, c.remark, c.nick_name, c.alias "
            "from chatroom_member cm join contact c on c.id = cm.member_id "
            "order by cm.room_id, c.username").fetchall()
    finally:
        conn.close()

    contacts = []
    by_id = {}
    for (cid, username, local_type, alias, remark, nick_name,
         big_url, small_url, head_md5, deleted, description) in rows:
        item = {
            "username": username or "",
            "displayName": display_name((remark, nick_name, alias, username)),
            "remark": remark or "",
            "nickname": nick_name or "",
            "alias": alias or "",
            "type": contact_type(username),
            "localType": local_type,
            "description": description or "",
            "deleted": bool(deleted),
            "headImageMd5": head_md5 or "",
            "headImageUrl": big_url or small_url or "",
            "headImage": "",
        }
        contacts.append(item)
        by_id[cid] = item
    by_username = {item["username"]: item for item in contacts if item["username"]}

    members_by_room = {}
    for room_id, username, remark, nick_name, alias in member_rows:
        members_by_room.setdefault(room_id, []).append({
            "username": username or "",
            "displayName": display_name((remark, nick_name, alias, username)),
            "remark": remark or "",
            "nickname": nick_name or "",
            "alias": alias or "",
        })

    groups = []
    for (rid, username, owner, remark, nick_name, alias) in room_rows:
        members = members_by_room.get(rid, [])
        groups.append({
            "username": username or "",
            "displayName": display_name((remark, nick_name, alias, username)),
            "owner": owner or "",
            "memberCount": len(members),
            "members": members,
        })

    avatar_count = 0
    avatar_dir = out / "avatars"
    if not args.no_avatars and head_db.is_file():
        avatar_dir.mkdir(parents=True, exist_ok=True)
        conn = connect_ro(head_db)
        try:
            for username, md5, data in conn.execute(
                    "select username, md5, image_buffer from head_image"):
                if not data:
                    continue
                try:
                    target = write_avatar(avatar_dir, username, md5, bytes(data))
                except OSError:
                    continue
                rel = target.relative_to(out).as_posix()
                item = by_username.get(username)
                if item is not None:
                    item["headImage"] = rel
                avatar_count += 1
        finally:
            conn.close()

    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    contacts_payload = {
        "schema": "wechat-export-contacts/v1",
        "generatedAt": generated,
        "counts": {"contacts": len(contacts), "groups": len(groups), "avatars": avatar_count},
        "contacts": contacts,
    }
    groups_payload = {
        "schema": "wechat-export-groups/v1",
        "generatedAt": generated,
        "groups": groups,
    }
    (out / "contacts.json").write_text(
        json.dumps(contacts_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "groups.json").write_text(
        json.dumps(groups_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "success": True,
        "output": str(out),
        "counts": contacts_payload["counts"],
        "contacts": str(out / "contacts.json"),
        "groups": str(out / "groups.json"),
        "avatarsDir": str(avatar_dir) if avatar_count else None,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"联系人 {len(contacts)}，群 {len(groups)}，头像 {avatar_count}")
        print(f"输出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
