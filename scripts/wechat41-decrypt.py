#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decrypt 4.1 SQLCipher DBs into a plaintext work directory.

Level 2 (4.1): the native backend reads plaintext sqlite like the 4.2 path.
This script only reads the source DBs and writes the decrypted copies under
`--work-dir`; it never modifies `xwechat_files`.

Key source follows weflow-cli's 4.1 config:
  ntKey/ntSalt        raw key+salt (same pair for all shards)
  decryptKey          passphrase (hex); each shard derives from its own header salt

Usage:
  python scripts/wechat41-decrypt.py --config ~/.weflow-cli/config.json \
    --work-dir ~/.wechat-export/wechat41/work
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.safety import assert_outside  # noqa: E402
from lib.v41db import export_plain, sqlcipher_available  # noqa: E402


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def fail(code: str, message: str, hint: str = ""):
    payload = {"success": False, "code": code, "error": message}
    if hint:
        payload["hint"] = hint
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 3


def find_db_storage(cfg: dict) -> tuple[Path, Path]:
    raw = cfg.get("ntDbPath") or cfg.get("dbPath") or ""
    if not raw:
        raise SystemExit("config 缺少 dbPath/ntDbPath")
    path = Path(raw).expanduser()
    if path.is_file():
        db_storage = path.parent.parent
    elif path.name == "db_storage" and path.is_dir():
        db_storage = path
    elif (path / "db_storage").is_dir():
        db_storage = path / "db_storage"
    else:
        candidates = [p for p in path.glob("*/db_storage") if p.is_dir()]
        wxid = str(cfg.get("wxid") or "")
        if wxid:
            matched = [p for p in candidates if p.parent.name.startswith(wxid)]
            if matched:
                candidates = matched
        if len(candidates) != 1:
            raise SystemExit(f"无法唯一确定 db_storage（候选 {len(candidates)} 个）: {path}")
        db_storage = candidates[0]
    return db_storage.resolve(), db_storage.parent.resolve()


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="解密 4.1 SQLCipher 数据库到 work/")
    ap.add_argument("--config", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not sqlcipher_available():
        return fail(
            "SQLCIPHER_MISSING",
            "需要 sqlcipher3 才能解密 4.1 数据库",
            "python -m pip install sqlcipher3-wheels",
        )

    cfg_path = Path(args.config).resolve()
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return fail("CONFIG_READ_FAILED", f"无法读取 config: {exc}",
                    "检查 --config 路径和 JSON 格式")

    try:
        db_storage, account_root = find_db_storage(cfg)
    except SystemExit as exc:
        return fail("CONFIG_INVALID", str(exc),
                    "config 的 dbPath/ntDbPath 需要指向 db_storage 或账号目录")
    work = Path(args.work_dir).resolve()
    try:
        assert_outside(account_root, work, "4.1 解密工作目录")
    except SystemExit as exc:
        return fail("WORK_DIR_INSIDE_ACCOUNT", str(exc),
                    "把 --work-dir 改到微信账号目录之外")

    key_hex = str(cfg.get("ntKey") or cfg.get("decryptKey") or "")
    salt_hex = str(cfg.get("ntSalt") or "")
    if cfg.get("ntKey") and cfg.get("ntSalt"):
        passphrase = ""
    else:
        passphrase = str(cfg.get("decryptKey") or cfg.get("favPassphrase") or "")
    if not passphrase and (not key_hex or not salt_hex):
        return fail(
            "CONFIG_KEY_MISSING",
            "config 缺少可用的 4.1 密钥",
            "需要 ntKey+ntSalt，或 decryptKey；运行 weflow-cli init 或补全 config",
        )

    dbs = []
    for path in sorted(db_storage.rglob("*.db")):
        rel = path.relative_to(db_storage)
        if rel.name.lower() == "message_fts.db":
            continue
        dbs.append((path, rel))

    ok = failed = skipped = 0
    errors = []
    for src, rel in dbs:
        dst = work / rel
        try:
            if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
                skipped += 1
                continue
            export_plain(src, dst, key_hex, salt_hex, passphrase)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append({"db": str(rel), "error": str(exc)})
            print(f"  ! {rel}: {exc}", file=sys.stderr, flush=True)
        else:
            print(f"  decrypt {rel}", file=sys.stderr, flush=True)

    core = re.compile(
        r'^(?:session/session\.db|contact/contact\.db|hardlink/hardlink\.db|'
        r'message/(?:message_|media_|message_resource).*\.db)$'
    )
    core_failed = [
        item for item in errors
        if core.match(str(item.get("db", "")).replace("\\", "/"))
    ]
    payload = {
        "success": not core_failed,
        "dbStorage": str(db_storage),
        "accountRoot": str(account_root),
        "workDir": str(work),
        "decrypt": {"ok": ok, "skipped": skipped, "failed": failed, "total": len(dbs)},
        "errors": errors[:20],
        "coreFailed": core_failed[:20],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not core_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
