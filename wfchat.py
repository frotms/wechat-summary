#!/usr/bin/env python
"""WeChat 4.x chat log reader for Claude Code.

Read-only: decrypts COPIES of db_storage under the export work dir, never writes
to WeChat's files. Reuses the proven independent SQLCipher path in
self-contained lib/ (no wcdb_api.dll, no screenshot, no protocol).

The DB passphrase is never printed. Resolve order:
  1. --key-file PATH          (64 hex chars, or 32 raw chars)
  2. $WECHAT_DB_KEY
  3. <this dir>/keys.json     {"accounts": {"<account dir>": "<hex>"}, "default": "<hex>"}
  4. WeFlow's config decryptKey (only if it validates for that account)

Commands:
  doctor
  sessions   [--limit N] [--since YYYY-MM-DD]
  messages   --session X [--since D] [--until D] [--last N] [--keyword K] [--types t1,t2] [--format json|text]
  search     --keyword K [--since D] [--limit N]
  media      --session X [--since D] [--until D] [--limit N]
  decrypt-image DAT [--out DIR]
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lib import v4db  # noqa: E402
from lib import wxdata as ec  # noqa: E402

ROOTS = [
    os.path.expanduser("~/xwechat_files"),
    os.path.expanduser("~/Documents/xwechat_files"),
    os.path.expanduser("~/Documents/WeChat Files"),
]


# ---------------------------------------------------------------- key handling

def _as_bytes(text):
    text = (text or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return bytes.fromhex(text)
    if len(text) == 32:
        return text.encode("utf-8")
    return None


def account_dirs():
    out = []
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isdir(os.path.join(d, "db_storage")):
                out.append(d)
    return out


def pick_account(requested):
    dirs = account_dirs()
    if requested:
        for d in dirs:
            if os.path.basename(d) == requested:
                return d
        cand = os.path.join(ROOTS[0], requested)
        if os.path.isdir(cand):
            return cand
        raise SystemExit(f"找不到账号目录: {requested}\n可用: {[os.path.basename(d) for d in dirs]}")
    if len(dirs) == 1:
        return dirs[0]
    keys = load_keys()
    for name in (keys.get("accounts") or {}):
        for d in dirs:
            if os.path.basename(d) == name:
                return d
    raise SystemExit("找到多个账号，请用 --account 指定: " + ", ".join(os.path.basename(d) for d in dirs))


def load_keys():
    p = os.path.join(HERE, "keys.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def key_candidates(account_dir, key_file):
    seen = []
    def add(v, tag):
        b = _as_bytes(v) if isinstance(v, str) else v
        if b and b not in seen:
            seen.append(b)
    if key_file:
        try:
            add(open(key_file, encoding="utf-8").read(), "key-file")
        except OSError as exc:
            raise SystemExit(f"读不到 --key-file: {exc}")
    add(os.environ.get("WECHAT_DB_KEY", ""), "env")
    keys = load_keys()
    add((keys.get("accounts") or {}).get(os.path.basename(account_dir), ""), "keys.json")
    add(keys.get("default", ""), "keys.json/default")
    cfg = getattr(ec, "WEFLOW_CONFIG", "")
    if cfg and os.path.isfile(cfg):
        try:
            add(v4db.load_weflow_key(cfg, ec.WEFLOW_USERDATA), "weflow-config")
        except Exception:
            pass
    return seen


def resolve_key(account_dir, key_file=None):
    db = os.path.join(account_dir, "db_storage", "session", "session.db")
    if not os.path.isfile(db):
        raise SystemExit(f"找不到 {db}")
    cands = key_candidates(account_dir, key_file)
    for key in cands:
        try:
            if v4db.validate_db(db, key):
                return key
        except Exception:
            continue
    raise SystemExit(
        "没有可用的数据库密钥。\n"
        f"试过 {len(cands)} 个候选都没通过 HMAC 校验。\n"
        "请把当前账号的 32 字节密钥写到本目录 keys.json：\n"
        '  {"accounts": {"wxid_xxx": "<64位hex>"}}\n'
        "或用 --key-file / 环境变量 WECHAT_DB_KEY 提供。"
    )


# ---------------------------------------------------------------- helpers

def day_start(text):
    return int(datetime.datetime.strptime(text, "%Y-%m-%d").timestamp())


def day_end(text):
    return day_start(text) + 86400


def open_account(args):
    account_dir = pick_account(getattr(args, "account", None))
    try:
        key = resolve_key(account_dir, getattr(args, "key_file", None))
        return account_dir, ec.WeChatData(account_dir, key)
    except SystemExit:
        # No stored passphrase: fall back to a read-only memory scan for the
        # per-database raw SQLCipher keys of the currently logged-in account.
        from lib import keyscan as keyscan_v4
        raw = keyscan_v4.collect_raw_keys(account_dir, verbose=True)
        if raw:
            sys.stderr.write(
                f"已通过只读内存扫描获得 {len(raw)} 个库的原始密钥（不显示、不落盘）\n"
            )
            return account_dir, ec.WeChatData(account_dir, None, raw_keys=raw)
        # Older/alternate layout: a single passphrase/master key.
        master = keyscan_v4.find_master_key(account_dir)
        if master:
            sys.stderr.write("已通过只读内存扫描获得主密钥（不显示、不落盘）\n")
            return account_dir, ec.WeChatData(account_dir, master)
        raise


def session_rows(data):
    conn = data.db("session", "session.db")
    return conn.execute(
        "select username, summary, sort_timestamp from SessionTable order by sort_timestamp desc"
    ).fetchall()


def find_session(data, needle):
    rows = session_rows(data)
    for r in rows:
        if r["username"] == needle:
            return r["username"]
    low = needle.lower()
    for r in rows:
        title = data.display_name(r["username"]) or ""
        if low in r["username"].lower() or low in title.lower():
            return r["username"]
    matches = [r["username"] for r in rows if low in (data.display_name(r["username"]) or "").lower()]
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(
        f"没有唯一匹配的会话: {needle}\n候选: " + ", ".join(
            f"{r['username']}({data.display_name(r['username'])})" for r in rows[:15]
        )
    )


def filter_messages(messages, args):
    """Single source of truth: wxexport.filter_messages."""
    import wxexport
    return wxexport.filter_messages(messages, args)


def print_messages(messages, as_json):
    if as_json:
        print(json.dumps(messages, ensure_ascii=False, indent=1))
        return
    for m in messages:
        who = "我" if m["is_self"] else (m["sender_name"] or m["sender"] or "?")
        print(f'[{m["time_str"]}] {who}: {m["text"]}')


# ---------------------------------------------------------------- media

MEDIA_KINDS = {"image", "video", "voice", "sticker", "app", "file"}

_HARDLINK = {"image": ("image_hardlink_info_v4", "Img"),
             "video": ("video_hardlink_info_v4", "Video"),
             "file": ("file_hardlink_info_v4", "File")}


class HardlinkIndex:
    """md5 -> on-disk media path, via db_storage/hardlink/hardlink.db."""

    def __init__(self, data, account_dir):
        self.account_dir = account_dir
        self._conn = None
        self._dirs = {}
        try:
            self._conn = data.db("hardlink", "hardlink.db")
        except Exception:
            self._conn = None

    def _dirname(self, ident):
        if ident in self._dirs:
            return self._dirs[ident]
        try:
            row = self._conn.execute("select username from dir2id where rowid=?", (ident,)).fetchone()
            name = row[0] if row else None
        except Exception:
            name = None
        self._dirs[ident] = name
        return name

    def path(self, md5, kind):
        if not self._conn or not md5 or kind not in _HARDLINK:
            return None
        table, sub = _HARDLINK[kind]
        try:
            row = self._conn.execute(
                f'select file_name, dir1, dir2 from "{table}" where md5=? limit 1', (md5,)
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        name, d1, d2 = row[0], self._dirname(row[1]), self._dirname(row[2])
        if not name:
            return None
        roots = []
        if kind == "image" and d1 and d2:
            roots.append(os.path.join(self.account_dir, "msg", "attach", d1, d2, sub))
        elif kind == "file":
            if d1:
                roots.append(os.path.join(self.account_dir, "msg", "file", d1))
            if d1 and d2:
                roots.append(os.path.join(self.account_dir, "msg", "attach", d1, d2, sub))
                roots.append(os.path.join(self.account_dir, "msg", "attach", d1, d2))
        elif kind == "video":
            if d1:
                roots.append(os.path.join(self.account_dir, "msg", "video", d1))
                roots.append(os.path.join(self.account_dir, "msg", "file", d1))
            if d1 and d2:
                roots.append(os.path.join(self.account_dir, "msg", "attach", d1, d2, sub))
        for root in roots:
            cand = os.path.join(root, name)
            if os.path.isfile(cand):
                return cand
        return None


def resolve_media_file(account_dir, name):
    """Find the on-disk media file by md5 stem or by original filename."""
    if not name:
        return None
    safe = glob.escape(name)
    # exact basename first, then "<name>.*", then fuzzy -- avoids wrong-year hits
    for pat in (f"**/{safe}", f"**/{safe}.*", f"**/{safe}*"):
        try:
            hits = glob.glob(os.path.join(account_dir, pat), recursive=True)
        except (OSError, re.error):
            hits = []
        hits = [h for h in hits if os.path.isfile(h)]
        if hits:
            hits.sort(key=lambda p: (os.path.basename(p) != name, -os.path.getsize(p)))
            return hits[0]
    return None


def cmd_media(args):
    account_dir, data = open_account(args)
    username = find_session(data, args.session)
    meta, messages = ec.collect(data, username)
    picked = [m for m in filter_messages(messages, args) if m["kind"] in MEDIA_KINDS]
    if args.limit:
        picked = picked[-args.limit:]
    rows = []
    links = HardlinkIndex(data, account_dir)
    try:
        import argparse as _ap
        import wxexport
        resolver = wxexport.MediaResolver(
            data, account_dir, ec.WORK,
            _ap.Namespace(media_mode="index", max_media_mb=0), username, data.own_username())
    except Exception:
        resolver = None
    for m in picked:
        extra = m.get("extra") or {}
        md5 = extra.get("md5") or ""
        path = None
        if resolver is not None:
            try:
                path = resolver.locate(m["kind"], extra, extra.get("title") or "", m)
            except Exception:
                path = None
        if not path:
            path = links.path(md5, m["kind"])
        if not path:
            path = resolve_media_file(account_dir, md5) or resolve_media_file(account_dir, extra.get("title"))
        rows.append({
            "time": m["time_str"], "kind": m["kind"], "type": m["type"],
            "sender": m["sender_name"] or m["sender"], "text": m["text"],
            "md5": md5, "title": extra.get("title"),
            "file": path,
        })
    print(json.dumps({"session": meta, "media": rows}, ensure_ascii=False, indent=1))
    return 0


def cmd_decrypt_image(args):
    """Decrypt one .dat with pure Python (no Node/native addon)."""
    import wxexport
    keys = load_keys()
    xor = args.xor if args.xor is not None else keys.get("image_xor")
    aes = args.aes or keys.get("image_aes")
    if (aes is None or xor is None) and getattr(args, "account", None):
        try:
            from lib import keyscan as keyscan_v4
            found = keyscan_v4.find_image_key(pick_account(args.account))
            if found:
                aes, xor = found
        except Exception:
            pass
    if aes is None:
        raise SystemExit("缺少 image_aes（--aes、keys.json 的 image_aes，或 --account 现场派生）")
    if xor is None:
        xor = 0
    try:
        plain, _kind = wxexport.decrypt_dat(args.dat, aes, int(xor))
    except wxexport.ImageKeyMissing as exc:
        raise SystemExit(str(exc))
    out = args.out or os.path.dirname(os.path.abspath(args.dat))
    os.makedirs(out, exist_ok=True)
    ext = wxexport._image_ext(plain)
    dst = os.path.join(out, os.path.splitext(os.path.basename(args.dat))[0] + ext)
    with open(dst, "wb") as f:
        f.write(plain)
    print(json.dumps({"ok": True, "out": dst, "bytes": len(plain), "ext": ext},
                     ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- commands

def cmd_doctor(args):
    info = {"engine": "lib", "work": ec.WORK, "accounts": []}
    for d in account_dirs():
        db = os.path.join(d, "db_storage", "session", "session.db")
        ok = None
        try:
            key = resolve_key(d, getattr(args, "key_file", None))
            ok = True
            info["accounts"].append({"dir": d, "session_db": os.path.isfile(db),
                                     "key": "ok", "key_len": len(key)})
        except SystemExit as exc:
            raw_dbs = 0
            try:
                from lib import keyscan as keyscan_v4
                raw_dbs = len(keyscan_v4.collect_raw_keys(d))
            except Exception:
                raw_dbs = 0
            if raw_dbs:
                info["accounts"].append({"dir": d, "session_db": os.path.isfile(db),
                                         "key": "raw-scan", "raw_dbs": raw_dbs})
            else:
                info["accounts"].append({"dir": d, "session_db": os.path.isfile(db),
                                         "key": "missing", "hint": str(exc).splitlines()[0]})
    info["keys_json"] = os.path.isfile(os.path.join(HERE, "keys.json"))
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


def cmd_sessions(args):
    account_dir, data = open_account(args)
    lo = day_start(args.since) if args.since else 0
    out = []
    for r in session_rows(data):
        if (r["sort_timestamp"] or 0) < lo:
            continue
        u = r["username"]
        if args.kind == "group" and not u.endswith("@chatroom"):
            continue
        if args.kind == "private" and (u.endswith("@chatroom") or u.startswith("gh_")):
            continue
        if args.kind == "official" and not u.startswith("gh_"):
            continue
        out.append({
            "username": u,
            "title": data.display_name(u),
            "last": ec.fmt_time(r["sort_timestamp"]) if r["sort_timestamp"] else "",
            "summary": (r["summary"] or "")[:80],
        })
        if len(out) >= args.limit:
            break
    print(json.dumps({"account": os.path.basename(account_dir), "sessions": out},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_messages(args):
    _dir, data = open_account(args)
    username = find_session(data, args.session)
    # Push the time window into SQL so huge sessions stay cheap.
    lo = day_start(args.since) if args.since else None
    hi = day_end(args.until) if args.until else None
    meta, messages = ec.collect(data, username, since=lo, until=hi)
    picked = filter_messages(messages, args)
    print_messages(picked, args.format == "json")
    sys.stderr.write(f"\n{meta['title']} · 命中 {len(picked)}/{len(messages)} 条\n")
    return 0


def cmd_search(args):
    _dir, data = open_account(args)
    lo = day_start(args.since) if args.since else 0
    hi = day_end(args.until) if args.until else None
    low = args.keyword.lower()
    hits = []
    for r in session_rows(data):
        if (r["sort_timestamp"] or 0) < lo:
            continue
        try:
            # Push the time window into SQL: loading a session's whole history
            # just to filter it in Python is what made search slow.
            meta, messages = ec.collect(data, r["username"], since=lo or None, until=hi)
        except Exception:
            continue
        for m in messages:
            if low in (m["text"] or "").lower():
                hits.append({"session": meta["title"], "username": r["username"],
                             "time": m["time_str"], "sender": m["sender_name"] or m["sender"],
                             "text": m["text"][:200]})
                if len(hits) >= args.limit:
                    break
        if len(hits) >= args.limit:
            break
    print(json.dumps(hits, ensure_ascii=False, indent=1))
    return 0


def cmd_diagnose(args):
    """Non-secret version/schema/media diagnostics for update triage."""
    import hashlib
    account_dir, data = open_account(args)
    from lib import keyscan as keyscan_v4
    info = {
        "account": os.path.basename(account_dir),
        "weixin": keyscan_v4.weixin_info(),
        "key_method": "raw-scan" if getattr(data, "raw_keys", None) else "passphrase",
        "raw_db_count": len(getattr(data, "raw_keys", {}) or {}),
        "schema": {},
        "media": {},
        "warnings": [],
    }
    known_versions = {"4.1.13.7"}
    ver = info["weixin"].get("version")
    info["weixin_known"] = bool(ver and ver in known_versions)
    if ver and not info["weixin_known"]:
        info["warnings"].append(
            f"未知微信版本 {ver}：key 偏移/图片 key/DB schema 可能已变化，先跑诊断再导出")

    for mod, name in (("session", "session.db"), ("contact", "contact.db"),
                      ("hardlink", "hardlink.db"), ("message", "message_0.db"),
                      ("message", "message_resource.db"), ("message", "media_0.db")):
        try:
            con = data.db(mod, name)
            tables = []
            for r in con.execute("select name from sqlite_master where type='table' order by name"):
                t = r[0]
                cols = [c[1] for c in con.execute('pragma table_info("%s")' % t)]
                tables.append(t + "(" + ",".join(cols) + ")")
            blob = "\n".join(tables).encode("utf-8")
            info["schema"][mod + "/" + name] = {
                "tables": len(tables),
                "sha256": hashlib.sha256(blob).hexdigest()[:16],
            }
        except Exception as exc:
            info["schema"][mod + "/" + name] = {"error": str(exc)[:120]}

    root = account_dir
    db_files = []
    for r, ds, fs in os.walk(os.path.join(root, "db_storage")):
        db_files += [os.path.join(r, f) for f in fs if f.endswith(".db")]
    info["db_files"] = len(db_files)
    info["wal_bytes"] = sum(os.path.getsize(p) for p in
                            [f + "-wal" for f in db_files] if os.path.isfile(p))

    for rel in ("msg/attach", "msg/video", "msg/file"):
        p = os.path.join(root, rel)
        n = 0
        if os.path.isdir(p):
            n = sum(len(fs) for _, _, fs in os.walk(p))
        info["media"][rel] = n
    for rel in ("ImageTemp", "Thumb"):
        n = 0
        for r, ds, fs in os.walk(os.path.join(root, "cache")):
            if os.path.basename(r) == rel:
                n += len(fs)
        info["media"]["cache/" + rel] = n

    try:
        ik = keyscan_v4.find_image_key(account_dir, allow_scan=False)
        info["image_key_method"] = "cfgDword" if ik else "none"
    except Exception as exc:
        info["image_key_method"] = "error"
        info["warnings"].append("image key: " + str(exc)[:120])
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


def cmd_clean(args):
    """Remove decrypted plaintext copies under the work dir (privacy hygiene)."""
    import shutil
    root = os.path.abspath(args.work or ec.WORK)
    removed = 0
    freed = 0
    targets = []
    if os.path.isdir(root):
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                size = sum(os.path.getsize(os.path.join(r, f))
                           for r, _d, fs in os.walk(p) for f in fs)
                targets.append(p)
                freed += size
    if args.dry_run:
        for p in targets:
            print(f"would remove {p}")
        print(f"total {freed / 1e6:.0f} MB")
        return 0
    for p in targets:
        shutil.rmtree(p, ignore_errors=True)
        removed += 1
    print(json.dumps({"removed_dirs": removed, "freed_mb": round(freed / 1e6, 1),
                      "work_dir": root}, ensure_ascii=False))
    return 0


def _iter_exports(out):
    """Yield (session_dir, meta_path, messages_path) for normal/flat exports."""
    for root, dirs, files in os.walk(out):
        if "chat.json" in files and "chat_messages.json" in files:
            yield root, os.path.join(root, "chat.json"), os.path.join(root, "chat_messages.json")
            dirs[:] = []
            continue
        for f in files:
            if f.startswith("chat_") and f.endswith(".json") \
                    and not f.endswith(("_messages.json", "_manifest.json")):
                base = f[:-5]
                mp = os.path.join(root, base + "_messages.json")
                if os.path.isfile(mp):
                    yield root, os.path.join(root, f), mp


def cmd_verify(args):
    """Check an export directory for missing messages/media."""
    import wxexport
    out = os.path.abspath(args.out)
    if not os.path.isdir(out):
        raise SystemExit(f"目录不存在: {out}")
    items = []
    deep_data = None
    if args.deep:
        deep_data = open_account(args)
    for sess_dir, meta_path, msg_path in _iter_exports(out):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
            messages = json.load(open(msg_path, encoding="utf-8"))
        except Exception as exc:
            items.append({"dir": sess_dir, "ok": False, "error": str(exc)})
            continue
        checks = {}
        checks["message_count"] = len(messages) == meta.get("message_count")
        missing_rel = []
        zero_size = []
        for m in messages:
            md = m.get("media") or {}
            rel = md.get("rel")
            if not rel:
                continue
            p = os.path.join(sess_dir, rel.replace("/", os.sep))
            if not os.path.isfile(p):
                missing_rel.append(rel)
            elif os.path.getsize(p) == 0:
                zero_size.append(rel)
        checks["media_files"] = not missing_rel and not zero_size
        checks["unhandled_kinds"] = not any(
            m.get("kind") not in wxexport.EXPORT_KINDS for m in messages)
        # Manifest can tell us whether media was intentionally skipped.
        manifest_path = os.path.join(sess_dir, "manifest.json")
        base_name = os.path.basename(meta_path)[:-5]
        if base_name != "chat":
            manifest_path = os.path.join(sess_dir, base_name + "_manifest.json")
        media_mode = "copy"
        if os.path.isfile(manifest_path):
            try:
                manifest = json.load(open(manifest_path, encoding="utf-8"))
                media_mode = manifest.get("media_mode", "copy")
            except Exception:
                pass
        media_msgs = [m for m in messages
                      if m.get("kind") in wxexport.MEDIA_KINDS]
        stat = [((m.get("media") or {}).get("status") or "none") for m in media_msgs]
        checks["media_status"] = (
            media_mode == "none" or all(s != "none" for s in stat))
        missing_ids = []
        extra_ids = []
        if deep_data:
            account_dir, data = deep_data
            f = meta.get("filters") or {}
            username = meta.get("username")
            try:
                since = wxexport.parse_when(f.get("since"))
                until = wxexport.parse_when(f.get("until"), until=True)
                _m2, source = ec.collect(data, username, since=since, until=until)
                ns = argparse.Namespace(
                    since=f.get("since"), until=f.get("until"), types=f.get("types"),
                    keyword=f.get("keyword"), sender=f.get("sender"), last=f.get("last"))
                source = wxexport.filter_messages(source, ns)
                src_ids = {(m.get("local_id"), m.get("server_id")) for m in source}
                exp_ids = {(m.get("local_id"), m.get("server_id")) for m in messages}
                missing_ids = sorted(src_ids - exp_ids, key=lambda x: (x[0] or 0, x[1] or 0))
                extra_ids = sorted(exp_ids - src_ids, key=lambda x: (x[0] or 0, x[1] or 0))
                checks["source_ids"] = not missing_ids and not extra_ids
            except Exception as exc:
                checks["source_ids"] = False
                missing_ids = [f"ERROR: {exc}"]
        item = {
            "dir": sess_dir,
            "title": meta.get("title"),
            "username": meta.get("username"),
            "messages": len(messages),
            "missing_rel": missing_rel[:20],
            "zero_size_rel": zero_size[:20],
            "missing_ids": missing_ids[:20],
            "extra_ids": extra_ids[:20],
            "checks": checks,
        }
        item["ok"] = all(checks.values())
        items.append(item)
    result = {"out": out, "sessions": len(items),
              "ok": all(i.get("ok") for i in items) and bool(items), "items": items}
    if not items:
        result["error"] = "没有找到 chat.json/chat_messages.json（verify 需要 --format json）"
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


def cmd_export(args):
    import wxexport
    return wxexport.run_export(args)


def main():
    ap = argparse.ArgumentParser(description="WeChat 4.x chat log reader (read-only)")
    ap.add_argument("--account")
    ap.add_argument("--key-file")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("diagnose").set_defaults(fn=cmd_diagnose)

    p = sub.add_parser("verify")
    p.add_argument("--out", required=True, help="导出目录")
    p.add_argument("--deep", action="store_true",
                   help="重新读取微信源库，对比消息 local_id/server_id")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("clean")
    p.add_argument("--work", help="要清理的工作目录（默认 $WECHAT_WORK_DIR）")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_clean)

    p = sub.add_parser("sessions")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--since")
    p.add_argument("--kind", choices=("all", "group", "private", "official"), default="all")
    p.set_defaults(fn=cmd_sessions)

    p = sub.add_parser("messages")
    p.add_argument("--session", required=True)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--last", type=int)
    p.add_argument("--keyword")
    p.add_argument("--types")
    p.add_argument("--format", choices=("json", "text"), default="text")
    p.set_defaults(fn=cmd_messages)

    p = sub.add_parser("search")
    p.add_argument("--keyword", required=True)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("media")
    p.add_argument("--session", required=True)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--last", type=int)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(fn=cmd_media)

    p = sub.add_parser("export")
    p.add_argument("--session", action="append", help="会话标题片段或 username，可重复/逗号分隔")
    p.add_argument("--all", action="store_true", help="导出全部匹配会话（必须显式指定）")
    p.add_argument("--kind", choices=("all", "group", "private", "official"), default="all")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--types", help="text,image,video,file,voice,sticker,app,system")
    p.add_argument("--keyword")
    p.add_argument("--sender", help="按发送者显示名/wxid 过滤（群聊）")
    p.add_argument("--last", type=int)
    p.add_argument("--out", help="输出目录（默认 $WECHAT_WORK_DIR/export_<时间>）")
    p.add_argument("--format", default="html,json", help="html,json,md,txt（逗号分隔）")
    p.add_argument("--media-mode", choices=("copy", "index", "none"), default="copy")
    p.add_argument("--voice-format", choices=("both", "wav", "silk"), default="both",
                   help="语音输出：both=SILK+WAV，wav=仅WAV，silk=仅原始SILK")
    p.add_argument("--fetch-articles", action="store_true",
                   help="联网抓取公众号(mp.weixin.qq.com)正文，默认关闭")
    p.add_argument("--download-stickers", action="store_true",
                   help="联网尝试下载本地缺失的表情，默认关闭")
    p.add_argument("--max-media-mb", type=int, default=0, help="单文件大小上限，0=不限")
    p.add_argument("--limit-sessions", type=int, default=0)
    p.add_argument("--flat", action="store_true", help="所有会话写入同一目录")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("decrypt-image")
    p.add_argument("dat")
    p.add_argument("--out")
    p.add_argument("--xor")
    p.add_argument("--aes")
    p.set_defaults(fn=cmd_decrypt_image)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
