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
  * with `--deep`, re-read the decrypted work DB and compare the exact
    `(local_id, server_id)` set with the raw JSON and the export.

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


_TABLE_SHARDS = {}


def table_shard_map(work: Path):
    """Cache Msg_* table -> shard path so each shard is opened once per run."""
    key = str(work)
    cached = _TABLE_SHARDS.get(key)
    if cached is not None:
        return cached
    mapping = {}
    for shard in sorted(work.glob("message/message_*.db")):
        try:
            conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            for (name,) in conn.execute(
                    "select name from sqlite_master where type='table' and name like 'Msg_%'"):
                mapping.setdefault(name, shard)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    _TABLE_SHARDS[key] = mapping
    return mapping


def count_plain(work: Path, username: str, since, until):
    """Count messages in the decrypted work DB."""
    table = table_name(username)
    shard = table_shard_map(work).get(table)
    if shard is None:
        return 0, {}
    total = 0
    by_type = {}
    try:
        conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0, {}
    try:
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
        pass
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


def format_id(pair):
    local_id, server_id = pair
    return f"{local_id}:{server_id}" if server_id else str(local_id)


def id_sort_key(pair):
    return (pair[0], pair[1])


def raw_ids(raw, since=None, until=None):
    """(localId, serverId) keys from the raw message JSON, within the window."""
    ids = set()
    invalid = 0
    for item in raw or []:
        local_id = item.get("localId")
        if local_id is None:
            invalid += 1
            continue
        try:
            local_id = int(local_id)
        except (TypeError, ValueError):
            invalid += 1
            continue
        ts = item.get("createTime")
        if ts is not None:
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                ts = None
            if ts is not None and (
                    (since is not None and ts < since)
                    or (until is not None and ts > until)):
                continue
        ids.add((local_id, str(item.get("serverId") or "")))
    return ids, invalid


def exported_ids(records):
    """(localId, serverId) keys from messages.jsonl."""
    ids = set()
    invalid = 0
    for record in records:
        local_id = record.get("localId")
        if local_id is None:
            invalid += 1
            continue
        try:
            local_id = int(local_id)
        except (TypeError, ValueError):
            invalid += 1
            continue
        ids.add((local_id, str(record.get("serverId") or "")))
    return ids, invalid


def coverage_boundary(coverage):
    """Export-time cursor recorded by native-backend coverage.boundary."""
    boundary = coverage.get("boundary") or {}
    ts = boundary.get("createTime")
    local_id = boundary.get("localId")
    if ts is None or local_id is None:
        return None
    try:
        return (int(ts), int(local_id))
    except (TypeError, ValueError):
        return None


def raw_max_boundary(raw, since=None, until=None):
    """Fallback cursor from raw messages (may hide trailing losses)."""
    best = None
    for item in raw or []:
        ts = item.get("createTime")
        local_id = item.get("localId")
        if ts is None or local_id is None:
            continue
        try:
            ts = int(ts)
            local_id = int(local_id)
        except (TypeError, ValueError):
            continue
        if (since is not None and ts < since) or (until is not None and ts > until):
            continue
        key = (ts, local_id)
        if best is None or key > best:
            best = key
    return best


def generated_boundary(bundle_dir: Path):
    """Fallback cursor from the bundle's generatedAt timestamp."""
    manifest = read_json(bundle_dir / "manifest.json") or {}
    value = manifest.get("generatedAt") or ""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return (int(stamp), None)


def source_ids(work: Path, username: str, since, until, limit=0, boundary=None):
    """Re-read the decrypted work DB and return the exact message-id set."""
    table = table_name(username)
    shard = table_shard_map(work).get(table)
    if shard is None:
        return set(), 0
    try:
        conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
    except sqlite3.Error:
        return set(), 0
    rows = []
    try:
        columns = {row[1] for row in conn.execute(f'pragma table_info("{table}")')}
        server_expr = "server_id" if "server_id" in columns else "''"
        conds, params = [], []
        if since is not None:
            conds.append("create_time >= ?")
            params.append(since)
        if until is not None:
            conds.append("create_time <= ?")
            params.append(until)
        if boundary is not None:
            ts, local_id = boundary
            if local_id is None:
                conds.append("create_time <= ?")
                params.append(ts)
            else:
                conds.append("(create_time < ? or (create_time = ? and local_id <= ?))")
                params += [ts, ts, local_id]
        where = (" where " + " and ".join(conds)) if conds else ""
        rows = conn.execute(
            f'select local_id, {server_expr}, create_time from "{table}"{where}',
            params).fetchall()
    except sqlite3.Error:
        return set(), 0
    finally:
        conn.close()
    rows.sort(key=lambda row: (int(row[2] or 0), int(row[0] or 0)), reverse=True)
    if limit and limit > 0:
        rows = rows[:int(limit)]
    ids = set()
    for local_id, server_id, _ts in rows:
        try:
            local_id = int(local_id)
        except (TypeError, ValueError):
            continue
        ids.add((local_id, str(server_id or "")))
    return ids, len(rows)


def deep_check(bundle_dir: Path, target_id: str, query: dict, work_dir: Path,
               records, raw, coverage, skipped: int):
    """Compare source DB, raw JSON, and exported JSONL by message identity."""
    since, until = window_from_query(query)
    limit = int(query.get("limit") or 0)

    boundary = coverage_boundary(coverage)
    boundary_source = "raw-coverage"
    reliable = boundary is not None
    if boundary is None:
        boundary = raw_max_boundary(raw, since, until)
        boundary_source = "raw-max"
    if boundary is None:
        boundary = generated_boundary(bundle_dir)
        boundary_source = "generatedAt"

    try:
        source_set, source_rows = source_ids(
            work_dir, target_id, since, until, limit, boundary)
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "ok": False, "error": str(exc)}

    raw_set, raw_invalid = raw_ids(raw, since, until)
    exp_set, exp_invalid = exported_ids(records)

    missing = source_set - raw_set
    extra = raw_set - source_set
    exported_not_raw = exp_set - raw_set
    raw_not_exported = raw_set - exp_set

    requested_types = query.get("types") or []
    type_mismatch = []
    if requested_types and "all" not in requested_types:
        allowed = set(requested_types)
        for record in records:
            kind = record.get("type")
            if kind and kind not in allowed:
                type_mismatch.append(record.get("id") or format_id(
                    (record.get("localId"), record.get("serverId") or "")))

    checks = {
        "sourceIdsMatchRaw": not missing and not extra,
        "exportedIdsInRaw": not exported_not_raw,
        "rawNotExportedMatchesSkipped": len(raw_not_exported) == skipped,
        "idsAvailable": raw_invalid == 0 and exp_invalid == 0,
        "typesMatchQuery": not type_mismatch,
    }
    return {
        "available": True,
        "ok": all(checks.values()),
        "boundary": {
            "createTime": boundary[0] if boundary else None,
            "localId": boundary[1] if boundary else None,
            "source": boundary_source,
            "reliable": reliable,
        },
        "source": {"messages": len(source_set), "rows": source_rows},
        "raw": {"messages": len(raw_set), "withoutIds": raw_invalid},
        "export": {
            "messages": len(exp_set),
            "withoutIds": exp_invalid,
            "skippedByType": skipped,
        },
        "checks": checks,
        "missingCount": len(missing),
        "missingIds": [format_id(pair) for pair in sorted(missing, key=id_sort_key)][:20],
        "extraCount": len(extra),
        "extraIds": [format_id(pair) for pair in sorted(extra, key=id_sort_key)][:20],
        "exportedNotInRawCount": len(exported_not_raw),
        "exportedNotInRaw": [
            format_id(pair) for pair in sorted(exported_not_raw, key=id_sort_key)][:20],
        "rawNotExportedCount": len(raw_not_exported),
        "typeMismatchCount": len(type_mismatch),
        "typeMismatch": type_mismatch[:20],
    }


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

    deep = None
    if mode.get("deep"):
        deep = deep_check(
            bundle_dir, target_id, query, Path(mode["work_dir"]),
            records, raw, coverage, skipped)

    clean = bool(
        not error
        and raw_count is not None
        and db_count is not None
        and chain_ok and source_ok
        and not duplicate_ids and outside == 0
        and coverage.get("mayHaveMore") is not True
        and (deep is None or bool(deep.get("ok")))
    )

    checks = {
        "sourceMatchesRaw": source_ok,
        "duplicateIds": duplicate_ids[:20],
        "outsideWindow": outside,
        "brokenMedia": broken_media[:50],
        "brokenMediaCount": len(broken_media),
        "coverageMayHaveMore": coverage.get("mayHaveMore") is True,
    }
    if deep is not None:
        checks["sourceIdsMatchRaw"] = bool(
            deep.get("checks", {}).get("sourceIdsMatchRaw"))

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
        "checks": checks,
        "deep": deep,
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
    ap.add_argument("--deep", action="store_true",
                    help="重新读取 work/ 源库，按 local_id+server_id 对账")
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
    if args.deep and mode["kind"] != "work":
        raise SystemExit("--deep 需要 --work-dir（已解密的 work/）；legacy weflow 模式不支持")
    mode["deep"] = bool(args.deep)

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
    deep_totals = None
    if args.deep:
        deep_totals = {
            "checkedTargets": len(results),
            "missingIds": sum((item.get("deep") or {}).get("missingCount", 0) for item in results),
            "extraIds": sum((item.get("deep") or {}).get("extraCount", 0) for item in results),
            "exportedNotInRaw": sum(
                (item.get("deep") or {}).get("exportedNotInRawCount", 0) for item in results),
            "typeMismatch": sum(
                (item.get("deep") or {}).get("typeMismatchCount", 0) for item in results),
        }
    report = {
        "schema": "wechat-export-audit/v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "output": str(output),
        "query": manifest.get("query"),
        "success": success,
        "totals": totals,
        "deep": deep_totals,
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
        if deep_totals is not None:
            print(f"  深度对账：源库缺 {deep_totals['missingIds']} 条，"
                  f"源库多 {deep_totals['extraIds']} 条，"
                  f"导出不在原始 {deep_totals['exportedNotInRaw']} 条，"
                  f"类型不符 {deep_totals['typeMismatch']} 条")
        problems = [item for item in results if not item["complete"]]
        for item in problems:
            source = item["source"]
            checks = item["checks"]
            deep = item.get("deep") or {}
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
            if deep.get("error"):
                reasons.append(f"深度对账失败: {deep['error']}")
            if deep.get("missingCount"):
                reasons.append(f"源库 {deep['missingCount']} 条不在原始 JSON")
            if deep.get("extraCount"):
                reasons.append(f"原始 JSON {deep['extraCount']} 条不在源库")
            if deep.get("exportedNotInRawCount"):
                reasons.append(f"导出 {deep['exportedNotInRawCount']} 条不在原始 JSON")
            deep_checks = deep.get("checks") or {}
            if deep and not deep_checks.get("rawNotExportedMatchesSkipped"):
                reasons.append("原始未导出条数 ≠ 按类型跳过条数")
            if deep and not deep_checks.get("typesMatchQuery"):
                reasons.append(f"导出类型与 --types 不符 {deep.get('typeMismatchCount', 0)} 条")
            if deep and not deep_checks.get("idsAvailable"):
                reasons.append("原始/导出缺少 localId/serverId，无法深度对账")
            print(f"  ✗ {item['target']['name']}: {'；'.join(reasons) or '不完整'}")
        if totals["brokenMedia"]:
            print(f"  提醒：导出 JSONL 引用了 {totals['brokenMedia']} 个磁盘上不存在的媒体文件")
        unreliable = [
            item for item in results
            if (item.get("deep") or {}).get("boundary", {}).get("reliable") is False
        ]
        if deep_totals is not None and unreliable:
            print(f"  提醒：{len(unreliable)} 个会话的深度边界来自兜底值，"
                  "尾部消息可能漏检；重新导出可写入 coverage.boundary")
        print(f"  报告: {output / 'audit.json'}")
    return 0 if success else 5


if __name__ == "__main__":
    raise SystemExit(main())
