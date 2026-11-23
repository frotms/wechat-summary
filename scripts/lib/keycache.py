# -*- coding: utf-8 -*-
"""Cache WeChat 4.2 per-DB raw keys for offline reuse.

The old main branch could reuse a user-managed keys.json / WeFlow config key.
WeChat 4.2 uses one raw key per DB, so the cache stores the whole
{rel_path: {enc_key, salt}} map produced by the vendor key extractor.

Security: this file contains decryption keys. It lives outside the WeChat
account directory, is written with best-effort 0600 permissions, and is kept by
`clean` unless `--purge-keys` is passed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

SCHEMA = "wechat-export-keys/v1"
PAGE_SIZE = 4096


def db_rels(data_root: Path, page_size: int = PAGE_SIZE):
    """Relative .db paths that the vendor key extractor would consider."""
    rels = []
    root = Path(data_root)
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".db"):
                continue
            path = Path(dirpath) / name
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            # WeChat keeps rotation/backup copies under *.db.factory/; the key
            # extractor does not return keys for them and exports never use them.
            if ".db.factory" in rel:
                continue
            try:
                if path.stat().st_size < page_size:
                    continue
                with path.open("rb") as handle:
                    handle.read(page_size)
            except OSError:
                continue
            rels.append(rel)
    return sorted(rels)


def load_keys(path: Path, expected_rels=None):
    """Return a usable key map, or None when missing/stale/invalid."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    keys = data.get("keys")
    if not isinstance(keys, dict) or not keys:
        return None
    for info in keys.values():
        if not isinstance(info, dict):
            return None
        if not str(info.get("enc_key") or ""):
            return None
        if not str(info.get("salt") or ""):
            return None
    if expected_rels is not None and set(keys) != set(expected_rels):
        return None
    return keys


def save_keys(path: Path, data_root: Path, account_root: Path, keys: dict):
    payload = {
        "schema": SCHEMA,
        "savedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataRoot": str(Path(data_root)),
        "accountRoot": str(Path(account_root)),
        "keys": keys,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target
