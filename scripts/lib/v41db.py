# -*- coding: utf-8 -*-
"""SQLCipher helpers for the 4.1 native path.

4.1 DBs are opened with a per-file raw key+salt.  When the config only carries
a passphrase, derive each shard's key from its own 16-byte file-header salt,
exactly like weflow-cli's `derive_database_key` (MIT).

The source DB is copied into a plaintext work directory with
`sqlcipher_export`; the native backend then reads that copy with plain sqlite3.
The source database is never modified.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def sqlcipher_available() -> bool:
    try:
        import sqlcipher3  # noqa: F401
        return True
    except Exception:
        return False


def derive_database_key(path, fallback_key: str, fallback_salt: str, passphrase: str = ""):
    """Per-shard key/salt; mirrors weflow-cli's 4.1 behavior."""
    if not passphrase:
        return fallback_key, fallback_salt
    try:
        with open(path, "rb") as handle:
            salt = handle.read(16)
        if len(salt) != 16:
            return fallback_key, fallback_salt
        raw = bytes.fromhex(passphrase)
        key = hashlib.pbkdf2_hmac("sha512", raw, salt, 256000, 32).hex()
        return key, salt.hex()
    except (OSError, ValueError):
        return fallback_key, fallback_salt


def is_plain_sqlite(path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _quote_path(path: str) -> str:
    return str(path).replace("'", "''")


def export_plain(src, dst, key_hex: str, salt_hex: str, passphrase: str = ""):
    """Decrypt `src` into `dst` using SQLCipher's sqlcipher_export."""
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if is_plain_sqlite(src):
        shutil.copy2(src, dst)
        return True
    try:
        import sqlcipher3
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("4.1 原生读取需要 sqlcipher3（pip install sqlcipher3-wheels）") from exc

    key, salt = derive_database_key(str(src), key_hex, salt_hex, passphrase)
    if not key or not salt:
        raise RuntimeError("config 缺少 ntKey/ntSalt/decryptKey")
    conn = sqlcipher3.connect(str(src))
    try:
        conn.execute(f'PRAGMA key = "x\'{key}{salt}\'";')
        # Force a read so a wrong key fails here, not at ATTACH time.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.execute(f"ATTACH DATABASE '{_quote_path(str(dst))}' AS plaintext KEY ''")
        conn.execute("SELECT sqlcipher_export('plaintext')")
        conn.execute("DETACH DATABASE plaintext")
        return True
    finally:
        conn.close()
