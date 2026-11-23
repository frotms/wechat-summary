#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a weflow-cli-compatible sandbox from a WeChat 4.2 account.

Pipeline:
  real encrypted DBs
    -> wechat-exporter.extract_keys (per-DB raw keys)
    -> wechat-exporter.decrypt_all (plaintext copies in work dir)
    -> re-encrypt every DB with ONE raw key+salt in a sandbox db_storage
    -> junction msg/cache/resource from the sandbox account to the real account
    -> write a sandbox HOME's .weflow-cli/config.json

Why re-encrypt: weflow-cli opens DBs with PRAGMA key = "x'<key><salt>'".
Using one raw key+salt for every DB lets weflow-cli read all shards without a
shared passphrase (which WeChat 4.2's per-DB keys do not expose).

The sandbox contains decrypted/re-encrypted copies; treat it as sensitive.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.safety import assert_outside  # noqa: E402
from lib.keycache import db_rels, load_keys, save_keys  # noqa: E402


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def import_tool(tool_dir: str = ""):
    """Use an explicit upstream clone when given; otherwise local vendor."""
    if tool_dir:
        tool = Path(tool_dir).resolve()
        if (tool / "app" / "core" / "keys.py").is_file():
            sys.path.insert(0, str(tool))
            from app.core.keys import extract_keys  # noqa: E402
            from app.core.decrypt import decrypt_all  # noqa: E402
            return extract_keys, decrypt_all
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))
    from vendor.wechat42.keys import extract_keys  # noqa: E402
    from vendor.wechat42.decrypt import decrypt_all  # noqa: E402
    return extract_keys, decrypt_all


def junction(link: Path, target: Path):
    if not target.exists():
        return False
    if link.exists():
        return True
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
        return True
    except Exception:
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
            return True
        except Exception:
            return False


def reencrypt_one(src: Path, dst: Path, raw: str) -> bool:
    if not src.is_file():
        return False
    if dst.exists():
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    import sqlcipher3
    conn = sqlcipher3.connect(str(src))
    try:
        conn.execute("PRAGMA cipher_page_size = 4096")
        conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
        conn.execute("ATTACH DATABASE ? AS enc KEY ?", (str(dst), raw))
        conn.execute("SELECT sqlcipher_export('enc')")
        conn.execute("DETACH DATABASE enc")
    finally:
        conn.close()
    check = sqlcipher3.connect(str(dst))
    try:
        check.execute(f'PRAGMA key = "{raw}"')
        check.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        check.close()
    return True


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="为 weflow-cli 构造微信 4.2 sandbox")
    ap.add_argument("--tool-dir", default="", help="可选：外部 wechat-exporter 源码目录；缺省用本地 vendor")
    ap.add_argument("--data-root", required=True, help="真实账号 db_storage")
    ap.add_argument("--account-root", required=True, help="真实账号目录（含 msg/cache）")
    ap.add_argument("--work-dir", required=True, help="明文数据库工作目录")
    ap.add_argument("--sandbox-dir", required=True)
    ap.add_argument("--home-dir", required=True, help="sandbox HOME（放 .weflow-cli/config.json）")
    ap.add_argument("--venv-python", default="", help="weflow-cli 用的 Python 解释器路径")
    ap.add_argument("--no-sandbox", action="store_true", help="只解密到 work/，不重建 sandbox（Level 2 原生导出）")
    ap.add_argument("--run-check", action="store_true", help="构造后运行 weflow-cli sessions")
    ap.add_argument("--keys-file", default="",
                    help="逐库 raw key 缓存（默认 <work 父目录>/keys.json）")
    ap.add_argument("--no-cache-keys", action="store_true", help="忽略已有 key 缓存")
    ap.add_argument("--no-save-keys", action="store_true", help="不写入 key 缓存")
    args = ap.parse_args()

    extract_keys, decrypt_all = import_tool(args.tool_dir)
    data_root = Path(args.data_root).resolve()
    account_root = Path(args.account_root).resolve()
    work = Path(args.work_dir).resolve()
    sandbox = Path(args.sandbox_dir).resolve()
    home = Path(args.home_dir).resolve()
    db_out = sandbox / "db_storage"
    # Read-only contract: no plaintext DB, re-encrypted sandbox or sandbox
    # home may live inside the WeChat account directory.
    for label, path in (("明文工作目录", work), ("sandbox", sandbox), ("sandbox home", home)):
        assert_outside(account_root, path, label)
    if args.no_sandbox:
        work.mkdir(parents=True, exist_ok=True)
    else:
        db_out.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)

    keys_file = (Path(args.keys_file).resolve() if args.keys_file
                 else work.parent / "keys.json")
    assert_outside(account_root, keys_file, "key 缓存")

    expected_rels = db_rels(data_root)
    keys = None
    meta = {}
    saved = False
    if not args.no_cache_keys:
        keys = load_keys(keys_file, expected_rels)
        if keys:
            meta = {"mode": "cache", "source": str(keys_file)}
            print(f"keys: {len(keys)} (cache)", file=sys.stderr)

    if keys:
        result = decrypt_all(str(data_root), str(work), keys,
                             progress=lambda p: print(f"  decrypt {p}%", file=sys.stderr, flush=True))
        if result.get("failed"):
            print("! key 缓存无法解密（微信可能已换 key）；重新从进程提取", file=sys.stderr)
            keys = None
            result = None

    if not keys:
        keys, meta = extract_keys(str(data_root))
        if not keys:
            print(json.dumps({
                "success": False,
                "code": "KEY_NOT_FOUND",
                "error": meta.get("message") or "未能提取到密钥",
                "hint": "登录微信一次以提取 key，或提供有效的 --keys-file",
                "meta": meta,
            }, ensure_ascii=False))
            return 2
        print(f"keys: {len(keys)} ({meta.get('mode')})", file=sys.stderr)
        # Force a rewrite: a failed cache attempt may have left partial files.
        result = decrypt_all(str(data_root), str(work), keys, skip_fresh=False,
                             progress=lambda p: print(f"  decrypt {p}%", file=sys.stderr, flush=True))

    if (not args.no_save_keys and meta.get("mode") != "cache"
            and result and result.get("failed") == 0):
        try:
            save_keys(keys_file, data_root, account_root, keys)
            saved = True
            print(f"key 缓存已保存: {keys_file}", file=sys.stderr)
        except OSError as exc:
            print(f"! key 缓存写入失败: {exc}", file=sys.stderr)

    key_summary = {
        "total": len(keys),
        "mode": meta.get("mode"),
        "source": "cache" if meta.get("mode") == "cache" else "process",
        "file": str(keys_file),
        "saved": saved,
    }
    if args.no_sandbox:
        print(json.dumps({
            "success": True,
            "mode": "native-only",
            "keys": key_summary,
            "decrypt": {"ok": result.get("ok"), "failed": result.get("failed"), "total": result.get("total")},
            "work": str(work),
            "sandbox": None,
            "home": None,
        }, ensure_ascii=False, indent=2))
        return 0

    key = secrets.token_hex(32)
    salt = secrets.token_hex(16)
    raw = f"x'{key}{salt}'"
    ok, failed, skipped = 0, [], 0
    for rel in sorted(keys):
        src = work / rel.replace("/", os.sep)
        if not src.is_file():
            skipped += 1
            continue
        dst = db_out / rel.replace("/", os.sep)
        try:
            if reencrypt_one(src, dst, raw):
                ok += 1
                print(f"  reencrypt {rel}", file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001
            failed.append({"rel": rel, "error": str(exc)})
            print(f"  ! {rel}: {exc}", file=sys.stderr, flush=True)

    # Media junctions: weflow-cli derives account_dir from ntDbPath, so the
    # sandbox account needs msg/cache/resource directories.
    linked = {}
    for name in ("msg", "cache", "resource", "business", "temp"):
        linked[name] = junction(sandbox / name, account_root / name)

    # Self wxid from the decrypted contact DB when available; WeChat 4.2 may
    # not have my_user_info, so fall back to the account folder name with its
    # 4-char suffix removed (e.g. mat_tranmer_3f87 -> mat_tranmer).
    wxid = ""
    contact_plain = work / "contact" / "contact.db"
    if contact_plain.is_file():
        try:
            c = sqlite3.connect(str(contact_plain))
            try:
                row = c.execute("select user_name from my_user_info limit 1").fetchone()
                if row:
                    wxid = row[0] or ""
            finally:
                c.close()
        except Exception:
            pass
    if not wxid:
        name = account_root.name
        parts = name.rsplit("_", 1)
        wxid = parts[0] if len(parts) == 2 and len(parts[1]) == 4 and parts[1].isalnum() else name

    config_dir = home / ".weflow-cli"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        # connect4x() requires dbPath to be non-empty, but for 4.x it is not a
        # database file. Point it at db_storage so deriveWeChatRoots takes the
        # sandbox account root, not the sandbox's parent.
        "dbPath": str(db_out),
        "dataVersion": "4.x",
        "wxid": wxid,
        "ntDbPath": str(db_out / "message" / "message_0.db"),
        "ntKey": key,
        "ntSalt": salt,
        "contactDbPath": str(db_out / "contact" / "contact.db"),
        "contactKey": key,
        "contactSalt": salt,
        "decryptKey": "",
        "decryptKey3x": "",
        "favPassphrase": "",
        "dbPath3x": "",
        "snsDbPath": "",
        "snsKey": "",
        "snsSalt": "",
        "favDbPath": "",
        "favKey": "",
        "favSalt": "",
        "wechatMediaDownload": "false",
    }
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(config_path, 0o600)
    except Exception:
        pass

    # weflow-cli probes for a Python interpreter with 5s/10s timeouts and
    # falls back to the Microsoft Store stub when the probe times out - which
    # happens under load (parallel ffmpeg sticker decoding). The sandbox HOME
    # hides the real venv, so write the path it should use into the cache
    # file weflow-cli reads first; then it never has to probe.
    if args.venv_python and Path(args.venv_python).is_file():
        (config_dir / "python-cmd.json").write_text(json.dumps({
            "cmd": str(Path(args.venv_python).resolve()),
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }), encoding="utf-8")

    summary = {
        "success": True,
        "keys": key_summary,
        "decrypt": {"ok": result.get("ok"), "failed": result.get("failed"), "total": result.get("total")},
        "reencrypt": {"ok": ok, "failed": failed, "skipped": skipped},
        "sandbox": str(sandbox),
        "home": str(home),
        "config": str(config_path),
        "junctions": linked,
        "wxid": bool(wxid),
    }

    if args.run_check:
        venv_scripts = Path.home() / ".weflow-cli" / "venv" / "Scripts"
        env = os.environ.copy()
        env["USERPROFILE"] = str(home)
        env["HOME"] = str(home)
        env["PATH"] = str(venv_scripts) + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        # Use the globally installed CLI; resolve via PATH.
        exe = shutil.which("weflow-cli.cmd") or shutil.which("weflow-cli")
        if not exe:
            summary["check"] = {"error": "weflow-cli not found"}
        else:
            proc = subprocess.run([exe, "sessions", "--json", "-n", "5"],
                                  env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=180)
            summary["check"] = {
                "exe": exe,
                "status": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-1000:],
            }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
