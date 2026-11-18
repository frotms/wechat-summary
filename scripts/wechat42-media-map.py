#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Produce a media map for one WeChat 4.2 session/time range.

weflow-cli's JSON export does not expose the video md5 for messages whose
`rawContent` is empty, so its own video scan cannot resolve those bodies.
`wechat-exporter` reads `packed_info_data` and can. This helper writes:

  { "items": { "<local_id>:<create_time>": {
      "kind": "video|image|file|voice",
      "media": "<absolute path or ''>",
      "video_md5": "...", "image_md5": "...", "poster": "..." } } }

Voice: `VoiceInfo` lives in `media_*.db` (not always media_0.db). SILK v3 is
decoded to 24 kHz mono WAV with `pysilk` (prebuilt wheel, no MSVC); `pilk` is
used as a fallback if present. The WAV path matches
`ChatStore._find_voice` so `wechat-exporter` can attach it to the message.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sqlite3
import sys
import wave
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.v2_decode import MAGIC, clean_account_id, decode_v2_file, find_codes  # noqa: E402
from lib.voice_decode import decode_silk_to_wav  # noqa: E402
from lib.safety import assert_outside  # noqa: E402


def ensure_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def prepare_voice_wavs(work: Path, target: str, voice_ids: set[int]) -> dict:
    """Decode the target's voice clips into `work/_voices/<safe target>/`."""
    stats = {"total": 0, "ok": 0, "failed": 0, "skipped": 0}
    if not voice_ids:
        return stats
    safe = re.sub(r'[\\/:*?"<>|]', "_", target)[:80]
    voices_dir = work / "_voices" / safe
    for db in sorted(glob.glob(str(work / "message" / "media_*.db"))):
        try:
            conn = sqlite3.connect(db)
            try:
                tables = {row[0] for row in conn.execute(
                    "select name from sqlite_master where type='table'")}
                if "VoiceInfo" not in tables or "Name2Id" not in tables:
                    continue
                names = {rid: name for rid, name in conn.execute(
                    "select rowid, user_name from Name2Id")}
                rows = conn.execute(
                    "select chat_name_id, create_time, local_id, voice_data "
                    "from VoiceInfo where voice_data is not null").fetchall()
            finally:
                conn.close()
        except Exception:
            continue
        for chat_id, create_time, local_id, blob in rows:
            if names.get(chat_id) != target or local_id not in voice_ids:
                continue
            stats["total"] += 1
            stamp = datetime.fromtimestamp(create_time).strftime("%Y%m%d_%H%M%S")
            out_path = str(voices_dir / f"{stamp}_{local_id}.wav")
            if os.path.exists(out_path):
                stats["skipped"] += 1
                continue
            if decode_silk_to_wav(bytes(blob), out_path):
                stats["ok"] += 1
            else:
                stats["failed"] += 1
    return stats


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="生成 4.2 会话媒体映射")
    ap.add_argument("--tool-dir", default="", help="可选：外部 wechat-exporter 源码目录；缺省用本地 vendor")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--account-root", required=True)
    ap.add_argument("--target", required=True, help="会话 username")
    ap.add_argument("--from", dest="since", default="")
    ap.add_argument("--to", dest="until", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.tool_dir:
        tool = Path(args.tool_dir).resolve()
        if (tool / "app" / "core" / "export.py").is_file():
            sys.path.insert(0, str(tool))
            from app.core.export import ChatStore  # noqa: E402
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from vendor.wechat42.export import ChatStore  # noqa: E402
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from vendor.wechat42.export import ChatStore  # noqa: E402

    work = Path(args.work_dir).resolve()
    account = Path(args.account_root).resolve()
    # Read-only contract: decoded voices/videos and the map must stay outside
    # the WeChat account directory.
    assert_outside(account, work, "明文工作目录")
    assert_outside(account, Path(args.out).resolve(), "媒体映射文件")
    store = ChatStore(
        str(work),
        images_dir=str(work / "_images"),
        voices_dir=str(work / "_voices"),
        my_wxid="",
        video_roots=[str(account / "msg" / "video"), str(account / "msg" / "attach")],
        video_cache=str(work / "video_index.json"),
    )
    store.refresh_video_index()
    msgs = store.messages(args.target, since=args.since or None, until=args.until or None)
    voice_ids = {m.get("local_id") for m in msgs if m.get("kind") == "voice"}
    voice_stats = prepare_voice_wavs(work, args.target, voice_ids)
    if voice_stats["ok"]:
        # Re-read so ChatStore._find_voice sees the new WAV files.
        msgs = store.messages(args.target, since=args.since or None, until=args.until or None)
    if voice_ids and not (voice_stats["ok"] or voice_stats["skipped"]):
        print("  ! 语音解码失败；安装 silk-python: pip install silk-python",
              file=sys.stderr, flush=True)

    items = {}
    counts = {}
    wxid = clean_account_id(account.name)
    v2_codes = None
    for m in msgs:
        kind = m.get("kind") or ""
        if kind not in ("image", "video", "file", "voice"):
            continue
        media = m.get("media") or ""
        if kind == "video" and media:
            try:
                with open(media, "rb") as handle:
                    is_v2 = handle.read(6) == MAGIC
            except OSError:
                is_v2 = False
            if is_v2:
                if v2_codes is None:
                    v2_codes = find_codes()
                decoded = decode_v2_file(str(media), str(work / "_videos_v2"), wxid, v2_codes)
                if decoded:
                    media = decoded
                    counts["video_v2_decoded"] = counts.get("video_v2_decoded", 0) + 1
        key = f"{m.get('local_id')}:{m.get('ts')}"
        items[key] = {
            "kind": kind,
            "time": m.get("time") or "",
            "sender": m.get("sender") or "",
            "media": media,
            "video_md5": m.get("video_md5") or "",
            "image_md5": m.get("image_md5") or "",
            "poster": m.get("poster") or "",
        }
        counts[kind] = counts.get(kind, 0) + 1
        if media:
            counts[kind + "_local"] = counts.get(kind + "_local", 0) + 1

    payload = {
        "schema": "wechat42-media-map/v1",
        "target": args.target,
        "range": {"since": args.since, "until": args.until},
        "counts": counts,
        "voice_prepare": voice_stats,
        "items": items,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "success": True,
        "target": args.target,
        "range": payload["range"],
        "counts": counts,
        "voice_prepare": voice_stats,
        "items": len(items),
        "out": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
