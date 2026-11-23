#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Level 2 native backend for WeChat 4.2 (no weflow-cli).

This implements the small subset of the `weflow-cli` contract that the Node
orchestrator needs:

  check --json
  sessions --json [--limit N]
  contacts --json [--limit N]
  export <talker> json [--media] [--from/--to/--date] --output <dir> --limit N

It reads the already-decrypted 4.2 `work/` directory (created by
wechat42-sandbox.py) through the vendored ChatStore. It does not read WeChat's
source databases, does not send/forward/edit anything, and writes only to the
requested export directory (and the work cache).

The shim `native-weflow.mjs` exposes this as a weflow-cli-compatible command so
`wechat-export.mjs` can keep its existing code path. 4.1 remains on weflow-cli
for now.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vendor.wechat42.export import ChatStore  # noqa: E402
from lib.v2_decode import MAGIC, clean_account_id, decode_v2_file, find_codes  # noqa: E402
from lib.net import fetch_article, fetch_sticker, image_ext  # noqa: E402
from lib.sticker import (  # noqa: E402
    any_sticker_file,
    derive_key as derive_sticker_key,
    find_seed as find_sticker_seed,
    prepare_sticker,
    sticker_cache_dirs,
)

try:
    from vendor.wechat42.media import prepare_images  # noqa: E402
except Exception:  # pragma: no cover
    prepare_images = None


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def fail(message: str, code: str = "NATIVE_ERROR", exit_code: int = 1):
    print(json.dumps({"success": False, "code": code, "error": message},
                     ensure_ascii=False))
    return exit_code


def config_path() -> Path:
    explicit = os.environ.get("WEFLOW_NATIVE_CONFIG", "")
    if explicit:
        return Path(explicit)
    return Path.home() / ".wechat-export" / "wechat42.json"


def context():
    cfg_path = config_path()
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"无法读取 4.2 配置 {cfg_path}: {exc}")
    work = Path(os.environ.get("WEFLOW_NATIVE_WORK") or cfg.get("workDir") or "").resolve()
    account = Path(os.environ.get("WEFLOW_NATIVE_ACCOUNT") or cfg.get("accountRoot") or "").resolve()
    if not work or not work.is_dir():
        raise SystemExit(f"work/ 不存在: {work}")
    if not account or not account.is_dir():
        raise SystemExit(f"账号目录不存在: {account}")
    return cfg, work, account


def make_store(work: Path, account: Path):
    store = ChatStore(
        str(work),
        images_dir=str(work / "_images"),
        voices_dir=str(work / "_voices"),
        my_wxid="",
        video_roots=[str(account / "msg" / "video"), str(account / "msg" / "attach")],
        video_cache=str(work / "video_index.json"),
    )
    info = store.self_info()
    store.my_wxid = info.get("wxid") or ""
    return store


def export_boundary(store, talker: str, since, until):
    """Newest (create_time, local_id) in the window, independent of parsing.

    Deep audit uses this to pin the export time: messages that arrive after
    the export are newer than this cursor and must not be reported missing.
    """
    db, table = store.find_table(talker)
    if not table:
        return None
    conds, params = [], []
    if since:
        conds.append("create_time >= ?")
        params.append(int(datetime.strptime(since, "%Y-%m-%d").timestamp()))
    if until:
        conds.append("create_time <= ?")
        params.append(int(datetime.strptime(until, "%Y-%m-%d").timestamp()) + 86399)
    where = (" where " + " and ".join(conds)) if conds else ""
    try:
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                f'select create_time, local_id from "{table}"{where} '
                'order by create_time desc, local_id desc limit 1', params).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {"createTime": int(row[0]), "localId": int(row[1])}


def safe_name(value: str, fallback: str = "file") -> str:
    name = os.path.basename(str(value or "")).strip() or fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")
    return name[:180] or fallback


def message_type(item):
    kind = item.get("kind") or "text"
    return kind if kind else "text"


def to_weflow(item):
    parsed = item.get("content") or {}
    raw = item.get("raw_content") or ""
    return {
        "localId": item.get("local_id"),
        "serverId": item.get("server_id") or "",
        "localType": item.get("local_type"),
        "createTime": item.get("ts"),
        "isSend": 1 if item.get("is_send") else 0,
        "senderUsername": item.get("sender_wxid") or "",
        "sender": item.get("sender") or "",
        "content": raw,
        "rawContent": raw,
        "parsedContent": item.get("text") or "",
        "messageType": message_type(item),
        "mediaKind": item.get("kind") if item.get("kind") in ("image", "file") else "",
        "appUrl": parsed.get("url") or "",
        "appTitle": parsed.get("title") or "",
        "appDescription": parsed.get("des") or "",
        "quote": parsed.get("quote") or None,
        "reply": parsed.get("reply") or "",
        "records": parsed.get("records") or [],
        "isNote": bool(parsed.get("is_note")),
        "amount": parsed.get("amount") or "",
        "lat": parsed.get("lat") or "",
        "lng": parsed.get("lng") or "",
        "cardUsername": parsed.get("username") or "",
    }


def prepare_target_images(store, work: Path, account: Path, msgs):
    """Decrypt only the image md5s referenced by these messages."""
    if prepare_images is None:
        return
    md5s = set()
    for item in msgs:
        if item.get("kind") != "image":
            continue
        md5 = str(item.get("image_md5") or "").lower()
        if md5:
            md5s.add(md5)
            stem = store.hardlink_stem(md5, "image")
            if stem:
                md5s.add(stem.lower())
    if not md5s:
        return
    attach = account / "msg" / "attach"
    if not attach.is_dir():
        return
    try:
        wxid = store.self_info().get("wxid") or clean_account_id(account.name)
        prepare_images(str(attach), str(work / "_images"), wxid, find_codes(),
                       only_md5s=md5s)
        store._image_index = None
    except Exception as exc:  # noqa: BLE001
        print(f"! [NATIVE_IMAGE_DECRYPT_FAILED] {exc}；图片将标记缺失", file=sys.stderr)


def prepare_target_voices(store, work: Path, target: str, msgs):
    """Decode the target's voice SILK rows to work/_voices."""
    if os.environ.get("WEFLOW_NATIVE_NO_VOICE") == "1":
        return
    voice_ids = {item.get("local_id") for item in msgs if item.get("kind") == "voice"}
    if not voice_ids:
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wechat42_media_map", HERE / "wechat42-media-map.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.prepare_voice_wavs(work, target, voice_ids)
    except Exception as exc:  # noqa: BLE001
        print(f"! [NATIVE_VOICE_DECODE_FAILED] {exc}；语音将标记缺失", file=sys.stderr)


def sticker_md5(item):
    raw = str(item.get("raw_content") or "")
    match = re.search(r'<emoji\b[^>]*md5="([0-9a-fA-F]{32})"', raw, re.IGNORECASE)
    if not match:
        match = re.search(r'\bmd5="([0-9a-fA-F]{32})"', raw)
    return match.group(1).lower() if match else ""


def prepare_target_stickers(work: Path, account: Path, msgs):
    """Decrypt cached custom stickers to work/_native_stickers."""
    if os.environ.get("WEFLOW_NATIVE_NO_STICKER") == "1":
        return {}
    ids = {sticker_md5(item) for item in msgs if item.get("kind") == "sticker"}
    ids.discard("")
    if not ids:
        return {}
    dirs = sticker_cache_dirs(str(account))
    sample = any_sticker_file(dirs)
    if not sample:
        return {}
    wxid = clean_account_id(account.name)
    seed_file = work / "_native_sticker_seed"
    seed = ""
    seed_source = "cache"
    try:
        seed = seed_file.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if not seed:
        seed_source = "process"
        found = find_sticker_seed(wxid, sample)
        if found:
            seed = str(found)
            try:
                seed_file.write_text(seed, encoding="utf-8")
            except OSError:
                pass
    if not seed:
        print("! [NATIVE_STICKER_SEED_MISSING] 没有拿到表情解密 seed；表情将标记缺失。"
              "提示：保持微信运行后重试，或配置 emoticonSeed/--sticker-seed。", file=sys.stderr)
        return {}
    key = derive_sticker_key(seed, wxid)
    out_dir = work / "_native_stickers"
    if seed_source == "cache":
        probe_id = next(iter(ids))
        probe, _mime = prepare_sticker(dirs, probe_id, key, str(out_dir))
        if not probe:
            print("! [NATIVE_STICKER_SEED_STALE] 缓存 seed 无法解密样本；重新探测",
                  file=sys.stderr)
            found = find_sticker_seed(wxid, sample)
            if not found:
                try:
                    seed_file.unlink()
                except OSError:
                    pass
                print("! [NATIVE_STICKER_SEED_MISSING] 旧 seed 失效且无法重新获取；"
                      "表情将标记缺失（保持微信运行或配置 emoticonSeed）", file=sys.stderr)
                return {}
            seed = str(found)
            key = derive_sticker_key(seed, wxid)
            try:
                seed_file.write_text(seed, encoding="utf-8")
            except OSError:
                pass
    result = {}
    for md5 in ids:
        path, _mime = prepare_sticker(dirs, md5, key, str(out_dir))
        if path:
            result[md5] = path
    return result



FILE_INDEX_SCHEMA = "wechat-export-file-index/v1"


def file_index(work: Path, account: Path):
    cache = work / "_native_file_index.json"
    roots = [account / "msg" / "file", account / "msg" / "attach"]
    root_names = [str(path) for path in roots]
    try:
        ttl = max(0, int(os.environ.get("WEFLOW_NATIVE_FILE_INDEX_TTL", "600")))
    except ValueError:
        ttl = 600
    force = os.environ.get("WEFLOW_NATIVE_REFRESH_FILE_INDEX") == "1"
    if cache.is_file() and not force:
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            built = float(payload.get("builtAt") or 0)
            if (payload.get("schema") == FILE_INDEX_SCHEMA
                    and payload.get("roots") == root_names
                    and time.time() - built <= ttl):
                return payload.get("files") or {}
        except (OSError, ValueError, TypeError) as exc:
            print(f"! [NATIVE_FILE_INDEX_INVALID] 文件索引缓存不可读（{exc}）；已重建",
                  file=sys.stderr)
    index = {}
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                low = name.lower()
                if low.endswith((".dat", ".mp4", ".thumb", ".db")):
                    continue
                index.setdefault(low, os.path.join(dirpath, name))
    try:
        cache.write_text(json.dumps({
            "schema": FILE_INDEX_SCHEMA,
            "builtAt": time.time(),
            "roots": root_names,
            "files": index,
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return index


def file_for(item, index):
    parsed = item.get("content") or {}
    title = str(parsed.get("title") or "").strip()
    if not title:
        text = str(item.get("text") or "")
        if text.startswith("[文件]"):
            title = text[4:].strip()
    if not title:
        return ""
    return index.get(os.path.basename(title).lower(), "")


def copy_asset(source: str, target_dir: Path, name: str):
    if not source or not os.path.isfile(source):
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name(name)
    if target.exists():
        stem, ext = os.path.splitext(target.name)
        target = target_dir / f"{stem}_{os.getpid()}{ext}"
    shutil.copy2(source, target)
    return target


def write_assets(store, msgs, output: Path, talker: str, account: Path, work: Path):
    assets_name = f"{talker}_assets"
    assets = output / assets_name
    manifest = {
        "schema": "weflow-media-manifest/v2",
        "messages": {},
        "documents": {"written": 0, "copied": 0, "unavailable": 0},
    }
    files = file_index(work, account)
    v2_codes = None
    wxid = clean_account_id(account.name)
    images_dir = assets / "images"
    files_dir = assets / "files"
    videos_dir = assets / "videos"
    voices_dir = assets / "voice"
    stickers_dir = assets / "stickers"
    articles_dir = assets / "articles"
    stickers = prepare_target_stickers(work, account, msgs)
    for item in msgs:
        key = f'{item.get("local_id")}:{item.get("ts")}'
        kind = item.get("kind")
        if kind == "image":
            source = item.get("media") or ""
            if not source:
                md5 = str(item.get("image_md5") or "")
                stem = store.hardlink_stem(md5, "image") or md5
                source = store._find_image(stem) or ""  # noqa: SLF001
            target = copy_asset(source, images_dir,
                                f'img_{item.get("local_id")}.{os.path.splitext(source)[1].lstrip(".") or "jpg"}')
            if target:
                manifest["messages"][key] = {
                    "path": f"{assets_name}/images/{target.name}",
                    "kind": "image",
                }
        elif kind == "file":
            source = file_for(item, files)
            title = (item.get("content") or {}).get("title") or item.get("text") or "file"
            target = copy_asset(source, files_dir, title)
            if target:
                manifest["messages"][key] = {
                    "path": f"{assets_name}/files/{target.name}",
                    "kind": "file",
                    "name": target.name,
                    "bytes": target.stat().st_size,
                }
                manifest["documents"]["written"] += 1
        elif kind == "video":
            source = item.get("media") or ""
            md5 = item.get("video_md5") or ""
            if source:
                try:
                    with open(source, "rb") as handle:
                        is_v2 = handle.read(6) == MAGIC
                except OSError:
                    is_v2 = False
                if is_v2:
                    if v2_codes is None:
                        v2_codes = find_codes()
                    decoded = decode_v2_file(
                        source, str(work / "_videos_v2"), wxid, v2_codes)
                    if decoded:
                        source = decoded
                    else:
                        print("! [NATIVE_V2_VIDEO_DECODE_FAILED] 视频是 V2 加密且无法解码；"
                              "将复制原始字节", file=sys.stderr)
            ext = os.path.splitext(source)[1] or ".mp4"
            target = copy_asset(source, videos_dir,
                                f'video_{item.get("local_id")}_{md5[:12]}{ext}')
            if target:
                entry = {
                    "path": f"{assets_name}/videos/{target.name}",
                    "kind": "video",
                    "name": target.name,
                    "bytes": target.stat().st_size,
                    "md5": md5,
                }
                poster = item.get("poster") or ""
                poster_name = (f'poster_{item.get("local_id")}_{md5[:8]}'
                               f'{os.path.splitext(poster)[1] or ".jpg"}')
                poster_target = copy_asset(poster, images_dir, poster_name)
                if poster_target:
                    entry["poster"] = f"{assets_name}/images/{poster_target.name}"
                manifest["messages"][key] = entry
        elif kind in ("link", "text"):
            if os.environ.get("WEFLOW_NATIVE_FETCH_ARTICLES") != "1":
                continue
            parsed = item.get("content") or {}
            raw = str(item.get("raw_content") or "")
            url = str(parsed.get("url") or "")
            if not url:
                match = re.search(r'https?://mp\.weixin\.qq\.com/[^\s<>"\']+', raw)
                url = match.group(0).rstrip(").,;，。；）") if match else ""
            if "mp.weixin.qq.com" not in url:
                continue
            try:
                page, title, text = fetch_article(url)
            except Exception as exc:  # noqa: BLE001
                print(f"! [NATIVE_ARTICLE_FETCH_FAILED] {exc}", file=sys.stderr)
                continue
            if not text:
                print("! [NATIVE_ARTICLE_EMPTY] 文章抓取成功但未提取到正文",
                      file=sys.stderr)
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            articles_dir.mkdir(parents=True, exist_ok=True)
            html_path = articles_dir / f"{digest}.html"
            txt_path = articles_dir / f"{digest}.txt"
            html_path.write_text(page, encoding="utf-8", errors="replace")
            txt_path.write_text(text or "", encoding="utf-8")
            manifest["messages"][key] = {
                "path": f"{assets_name}/articles/{txt_path.name}",
                "html": f"{assets_name}/articles/{html_path.name}",
                "kind": "article",
                "title": title or str(parsed.get("title") or ""),
                "url": url,
                "bytes": txt_path.stat().st_size,
            }
        elif kind == "voice":
            if os.environ.get("WEFLOW_NATIVE_NO_VOICE") == "1":
                continue
            source = item.get("media") or ""
            target = copy_asset(source, voices_dir,
                                f'voice_{item.get("local_id")}_{item.get("ts")}.wav')
            if target:
                manifest["messages"][key] = {
                    "path": f"{assets_name}/voice/{target.name}",
                    "kind": "voice",
                    "bytes": target.stat().st_size,
                }
        elif kind == "sticker":
            if os.environ.get("WEFLOW_NATIVE_NO_STICKER") == "1":
                continue
            md5 = sticker_md5(item)
            source = stickers.get(md5, "")
            ext = os.path.splitext(source)[1] or ".png"
            target = copy_asset(source, stickers_dir, f"sticker_{md5}{ext}")
            if not target and os.environ.get("WEFLOW_NATIVE_DOWNLOAD_STICKERS") == "1":
                try:
                    data, ext = fetch_sticker(str(item.get("raw_content") or ""))
                except Exception as exc:  # noqa: BLE001
                    print(f"! [NATIVE_STICKER_DOWNLOAD_FAILED] {exc}", file=sys.stderr)
                    continue
                stickers_dir.mkdir(parents=True, exist_ok=True)
                stem = md5 or hashlib.sha1(data).hexdigest()[:12]
                target = stickers_dir / f"sticker_{stem}{ext}"
                target.write_bytes(data)
            if target:
                manifest["messages"][key] = {
                    "path": f"{assets_name}/stickers/{target.name}",
                    "kind": "sticker",
                    "sticker_md5": md5,
                    "bytes": target.stat().st_size,
                }
    (assets).mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"assets": {"dir": assets_name, "items": len(manifest["messages"])}}


def cmd_check():
    _cfg, work, account = context()
    deps = {}
    for name in ("zstandard", "cryptography"):
        try:
            __import__(name)
            deps[name] = True
        except Exception:
            deps[name] = False
    payload = {
        "success": True,
        "schema": "weflow-check/v1",
        "runtime": {
            "node": {"available": True, "version": ""},
            "python": {"available": True, "version": sys.version.split()[0]},
        },
        "dependencies": {"required": deps, "optional": {}},
        "configuration": {
            "initialized": (work / "message").is_dir(),
            "messageDatabase": (work / "message").is_dir(),
        },
        "wechatClient": {
            "running": True,
            "version": "4.2 (native)",
            "dataDirs": [str(account)],
            "compatibility": {"verdict": "native"},
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_sessions(args):
    _cfg, work, account = context()
    store = make_store(work, account)
    rows = []
    limit = int(args.limit or 0)
    for item in store.sessions():
        rows.append({
            "username": item.get("username") or "",
            "displayName": item.get("name") or item.get("username") or "",
            "remark": "",
            "nickname": item.get("name") or "",
            "lastTimestamp": int(item.get("last_time") or 0),
            "unreadCount": 0,
            "summary": item.get("summary") or "",
        })
        if limit and len(rows) >= limit:
            break
    print(json.dumps({"success": True, "sessions": rows}, ensure_ascii=False))
    return 0


def cmd_contacts(args):
    _cfg, work, account = context()
    rows = []
    db = work / "contact" / "contact.db"
    if db.is_file():
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                for username, remark, nick in conn.execute(
                        "select username, remark, nick_name from contact"):
                    rows.append({
                        "username": username or "",
                        "displayName": remark or nick or username or "",
                        "remark": remark or "",
                        "nickname": nick or "",
                        "alias": "",
                    })
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    limit = int(args.limit or 0)
    if limit:
        rows = rows[:limit]
    print(json.dumps({"success": True, "contacts": rows}, ensure_ascii=False))
    return 0


def cmd_export(args):
    _cfg, work, account = context()
    store = make_store(work, account)
    since = args.date or args.since or None
    until = args.date or args.until or None
    limit = int(args.limit or 0) or None
    if (args.media or args.refresh_video_index
            or os.environ.get("WEFLOW_NATIVE_REFRESH_VIDEO_INDEX") == "1"):
        store.refresh_video_index()
    msgs = store.messages(args.talker, since=since, until=until, limit=limit)
    boundary = export_boundary(store, args.talker, since, until)
    if not msgs:
        print(json.dumps({"success": False, "code": "NO_MESSAGES",
                          "error": "未找到消息"}, ensure_ascii=False))
        return 1
    if args.media:
        prepare_target_voices(store, work, args.talker, msgs)
        prepare_target_images(store, work, account, msgs)
        msgs = store.messages(args.talker, since=since, until=until, limit=limit)
    rows = [to_weflow(item) for item in msgs]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{args.talker}_messages.json"
    coverage = {
        "requestedLimit": int(args.limit or 0),
        "returned": len(rows),
        "mayHaveMore": bool(limit and len(rows) >= limit),
        "boundary": boundary,
    }
    path.write_text(json.dumps({
        "schema": "weflow-message/v1",
        "source": "native-backend",
        "generatedAt": "",
        "coverage": coverage,
        "messages": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "success": True,
        "format": "json",
        "contract": "weflow-v1",
        "path": str(path),
        "count": len(rows),
    }
    if args.media:
        result["media"] = write_assets(store, msgs, output, args.talker, account, work)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main():
    ensure_utf8()
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-v"):
        print("native-4.2")
        return 0
    argv = sys.argv[1:]
    if not argv:
        return fail("缺少命令")
    command = argv[0]
    if command == "check":
        return cmd_check()
    if command == "media":
        print(json.dumps({"success": True, "total": 0, "rows": []}, ensure_ascii=False))
        return 0
    if command == "sessions":
        ap = argparse.ArgumentParser()
        ap.add_argument("--limit", type=int, default=1000)
        ap.add_argument("--json", action="store_true")
        return cmd_sessions(ap.parse_args(argv[1:]))
    if command == "contacts":
        ap = argparse.ArgumentParser()
        ap.add_argument("--limit", type=int, default=5000)
        ap.add_argument("--json", action="store_true")
        return cmd_contacts(ap.parse_args(argv[1:]))
    if command == "export":
        ap = argparse.ArgumentParser()
        ap.add_argument("talker")
        ap.add_argument("format", choices=("json",))
        ap.add_argument("--date")
        ap.add_argument("--from", dest="since")
        ap.add_argument("--to", dest="until")
        ap.add_argument("--limit", type=int, default=0)
        ap.add_argument("--output", required=True)
        ap.add_argument("--media", action="store_true")
        ap.add_argument("--refresh-video-index", action="store_true")
        ap.add_argument("--contract", default="weflow-v1")
        ap.add_argument("--non-interactive", action="store_true")
        ap.add_argument("--json", action="store_true")
        return cmd_export(ap.parse_args(argv[1:]))
    return fail(f"不支持的命令: {command}", "UNSUPPORTED_COMMAND")


if __name__ == "__main__":
    raise SystemExit(main())
