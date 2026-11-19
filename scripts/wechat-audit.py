#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit an exported chat bundle for completeness.

This is the independent cross-check for "did we get every message?":

  * count the rows in the WeChat message DB for the exported time window
    (4.2: decrypted work DB; 4.1: encrypted DB through weflow-cli helpers),
  * compare that count with the raw JSON that weflow-cli returned,
  * compare that with `messages.jsonl` + skipped-by-type,
  * check for duplicate ids, messages outside the window, and media files
    referenced by the JSONL but missing on disk.

Output is a JSON report (also written to `<export>/audit.json`). Exit code is
0 when the message chain is complete, 5 when something is missing.

The audit only reads the database and the export. It does not modify WeChat
data and does not forward anything.
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


def parse_bound(value: str, end_of_day: bool):
    if not value:
        return None
    ts = int(datetime.strptime(value, "%Y-%m-%d").timestamp())
    return ts + 86399 if end_of_day else ts


def window_from_query(query: dict):
    """(since, until) from a bundle manifest's query block."""
    date = query.get("date") or ""
    if date:
        return parse_bound(date, False), parse_bound(date, True)
    return parse_bound(query.get("from") or "", False), parse_bound(query.get("to") or "", True)


def table_name(username: str) -> str:
    return "Msg_" + hashlib.md5(str(username).encode()).hexdigest()


def count_plain(work: Path, username: str, since, until):
    """Count messages in the decrypted 4.2 work DB."""
    table = table_name(username)
    total = 0
    by_type = {}
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
            conds, params = [], []
            if since is not None:
                conds.append("create_time >= ?")
                params.append(since)
            if until is not None:
                conds.append("create_time <= ?")
                params.append(until)
            where = (" where " + " and ".join(conds)) if conds else ""
            for local_type, n in conn.execute(
                    f'select local_type, count(*) from "{table}"{where} group by local_type', params):
                base = int(local_type) & 0xFFFFFFFF
                by_type[base] = by_type.get(base, 0) + int(n)
                total += int(n)
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return total, by_type


def count_encrypted(scripts_dir: Path, config_path: Path, username: str, since, until):
    """Count messages through weflow-cli's encrypted-DB helpers (4.1 path)."""
    sys.path.insert(0, str(scripts_dir))
    from export_chat_html import connect, derive_database_key  # noqa: E402

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db_path = cfg.get("ntDbPath") or ""
    key_hex = cfg.get("ntKey") or cfg.get("decryptKey") or ""
    salt_hex = cfg.get("ntSalt") or ""
    passphrase = cfg.get("favPassphrase") or cfg.get("decryptKey") or ""
    if not db_path or not key_hex or not salt_hex:
        raise SystemExit("config 缺少 ntDbPath/ntKey/ntSalt")

    table = table_name(username)
    total = 0
    by_type = {}
    for shard in sorted(Path(db_path).parent.glob("message_*.db")):
        try:
            shard_key, shard_salt = derive_database_key(str(shard), key_hex, salt_hex, passphrase)
            conn, cur = connect(str(shard), shard_key, shard_salt)
        except Exception:
            continue
        try:
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                continue
            conds, params = [], []
            if since is not None:
                conds.append("create_time >= ?")
                params.append(since)
            if until is not None:
                conds.append("create_time <= ?")
                params.append(until)
            where = (" WHERE " + " and ".join(conds)) if conds else ""
            cur.execute(f'SELECT local_type, count(*) FROM "{table}"{where} GROUP BY local_type', params)
            for local_type, n in cur.fetchall():
                base = int(local_type) & 0xFFFFFFFF
                by_type[base] = by_type.get(base, 0) + int(n)
                total += int(n)
        except Exception:
            continue
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return total, by_type


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_jsonl(path: Path):
    rows = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except (OSError, ValueError):
        return None
    return rows


def raw_messages(bundle_dir: Path, target_id: str):
    """The raw weflow JSON (pre type-filter) and its coverage block."""
    candidates = [
        bundle_dir / "raw" / f"{target_id}_messages.json",
    ]
    candidates.extend(sorted((bundle_dir / "raw").glob("*_messages.json")))
    for path in candidates:
        data = read_json(path)
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return data["messages"], data.get("coverage") or {}, path.name
        if isinstance(data, list):
            return data, {}, path.name
    return None, {}, ""


def media_paths(record):
    media = record.get("media") or {}
    for key in ("image", "file", "video", "voice", "sticker", "poster"):
        value = media.get(key)
        if value:
            yield key, value


def audit_bundle(bundle_dir: Path, bundle: dict, query: dict, mode: dict):
    target = bundle.get("target") or {}
    target_id = target.get("id") or ""
    since, until = window_from_query(query)

    db_count, db_by_type = None, {}
    error = ""
    try:
        if mode["kind"] == "work":
            db_count, db_by_type = count_plain(Path(mode["work_dir"]), target_id, since, until)
        elif mode["kind"] == "weflow":
            db_count, db_by_type = count_encrypted(
                Path(mode["weflow_cli_dir"]), Path(mode["config"]), target_id, since, until)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    raw, coverage, raw_file = raw_messages(bundle_dir, target_id)
    records = read_jsonl(bundle_dir / "messages.jsonl")
    if records is None:
        records = []

    raw_count = len(raw) if raw is not None else None
    exported = len(records)
    skipped = int((bundle.get("stats") or {}).get("skippedByType") or 0)
    # An empty time range legitimately has no raw JSON file: weflow-cli returns
    # "未找到消息" and the orchestrator writes no messages file. If the DB also
    # has no rows and nothing was exported, that is complete, not missing.
    if raw is None and db_count == 0 and exported == 0 and skipped == 0:
        raw_count = 0

    duplicate_ids = []
    seen = set()
    outside = 0
    broken_media = []
    for record in records:
        rid = record.get("id")
        if rid in seen:
            duplicate_ids.append(rid)
        seen.add(rid)
        ts = record.get("timestamp")
        if ts is not None and ((since is not None and ts < since) or (until is not None and ts > until)):
            outside += 1
        for kind, rel in media_paths(record):
            if not (bundle_dir / rel).is_file():
                broken_media.append({"id": rid, "kind": kind, "path": rel})

    chain_ok = raw_count is not None and (exported + skipped == raw_count)
    source_ok = db_count is not None and raw_count is not None and db_count == raw_count
    clean = bool(
        not error
        and raw_count is not None
        and db_count is not None
        and chain_ok and source_ok
        and not duplicate_ids and outside == 0
        and coverage.get("mayHaveMore") is not True
    )

    return {
        "target": {"id": target_id, "name": target.get("name") or target_id},
        "query": {"from": query.get("from") or query.get("date"), "to": query.get("to") or query.get("date")},
        "source": {
            "kind": mode["kind"],
            "dbMessages": db_count,
            "dbByType": {str(k): v for k, v in sorted(db_by_type.items())},
            "rawMessages": raw_count,
            "rawFile": raw_file,
            "coverage": coverage,
            "error": error or None,
        },
        "export": {
            "exportedMessages": exported,
            "skippedByType": skipped,
            "chainMatchesRaw": chain_ok,
        },
        "checks": {
            "sourceMatchesRaw": source_ok,
            "duplicateIds": duplicate_ids[:20],
            "outsideWindow": outside,
            "brokenMedia": broken_media[:50],
            "brokenMediaCount": len(broken_media),
            "coverageMayHaveMore": coverage.get("mayHaveMore") is True,
        },
        "complete": clean,
    }


def load_bundles(output: Path):
    manifest = read_json(output / "manifest.json")
    if not manifest or not isinstance(manifest.get("bundles"), list):
        raise SystemExit(f"不是有效的导出目录（缺少 manifest.json/bundles）: {output}")
    bundles = []
    for entry in manifest["bundles"]:
        bundle_dir = output / str(entry.get("dir") or "")
        bundle_manifest = read_json(bundle_dir / "manifest.json") or {}
        query = bundle_manifest.get("query") or manifest.get("query") or {}
        bundles.append((bundle_dir, entry, query))
    return manifest, bundles


def main():
    ensure_utf8()
    ap = argparse.ArgumentParser(description="校验微信导出是否完整")
    ap.add_argument("--output", required=True, help="导出根目录（含 manifest.json）")
    ap.add_argument("--work-dir", default="", help="4.2：已解密的工作目录")
    ap.add_argument("--weflow-cli-dir", default="", help="4.1：weflow-cli scripts 目录")
    ap.add_argument("--config", default="", help="4.1：weflow-cli config.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    output = Path(args.output).resolve()
    if args.work_dir:
        mode = {"kind": "work", "work_dir": str(Path(args.work_dir).resolve())}
    elif args.weflow_cli_dir and args.config:
        mode = {
            "kind": "weflow",
            "weflow_cli_dir": str(Path(args.weflow_cli_dir).resolve()),
            "config": str(Path(args.config).resolve()),
        }
    else:
        raise SystemExit("需要 --work-dir（4.2）或 --weflow-cli-dir + --config（4.1）")

    manifest, bundles = load_bundles(output)
    results = []
    for bundle_dir, entry, query in bundles:
        results.append(audit_bundle(bundle_dir, entry, query, mode))

    totals = {
        "targets": len(results),
        "completeTargets": sum(1 for item in results if item["complete"]),
        "dbMessages": sum(item["source"]["dbMessages"] or 0 for item in results),
        "rawMessages": sum(item["source"]["rawMessages"] or 0 for item in results),
        "exportedMessages": sum(item["export"]["exportedMessages"] for item in results),
        "skippedByType": sum(item["export"]["skippedByType"] for item in results),
        "brokenMedia": sum(item["checks"]["brokenMediaCount"] for item in results),
        "duplicateIds": sum(len(item["checks"]["duplicateIds"]) for item in results),
        "outsideWindow": sum(item["checks"]["outsideWindow"] for item in results),
    }
    success = all(item["complete"] for item in results)
    report = {
        "schema": "wechat-export-audit/v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "output": str(output),
        "query": manifest.get("query"),
        "success": success,
        "totals": totals,
        "targets": results,
    }
    try:
        (output / "audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"完整性校验：{totals['completeTargets']}/{totals['targets']} 个会话通过")
        print(f"  数据库消息 {totals['dbMessages']}，原始 {totals['rawMessages']}，"
              f"导出 {totals['exportedMessages']}，按类型跳过 {totals['skippedByType']}")
        problems = [item for item in results if not item["complete"]]
        for item in problems:
            source = item["source"]
            checks = item["checks"]
            reasons = []
            if source["error"]:
                reasons.append(f"读库失败: {source['error']}")
            if source["dbMessages"] is not None and source["rawMessages"] is not None \
                    and source["dbMessages"] != source["rawMessages"]:
                reasons.append(f"库 {source['dbMessages']} ≠ 原始 {source['rawMessages']}")
            if not item["export"]["chainMatchesRaw"]:
                reasons.append("导出+跳过 ≠ 原始")
            if checks["duplicateIds"]:
                reasons.append(f"重复 id {len(checks['duplicateIds'])} 个")
            if checks["outsideWindow"]:
                reasons.append(f"时间窗外 {checks['outsideWindow']} 条")
            if checks["coverageMayHaveMore"]:
                reasons.append("coverage.mayHaveMore=true")
            print(f"  ✗ {item['target']['name']}: {'；'.join(reasons) or '不完整'}")
        if totals["brokenMedia"]:
            print(f"  提醒：导出 JSONL 引用了 {totals['brokenMedia']} 个磁盘上不存在的媒体文件")
        print(f"  报告: {output / 'audit.json'}")
    return 0 if success else 5


if __name__ == "__main__":
    raise SystemExit(main())
