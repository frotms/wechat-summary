# -*- coding: utf-8 -*-
"""WeChat V2 media decoding (AES-128-ECB + XOR tail), shared by scripts."""
from __future__ import annotations

import glob
import hashlib
import os
import re
import struct

from Crypto.Cipher import AES
from Crypto.Util import Padding

MAGIC = b"\x07\x08V2\x08\x07"
HEADER_SIZE = 15


def find_codes(appdata: str = "") -> list[str]:
    root = appdata or os.environ.get("APPDATA", "")
    if not root:
        return []
    pattern = os.path.join(root, "Tencent", "xwechat", "*", "kvcomm", "key_*_*.statistic")
    codes = set()
    for path in glob.glob(pattern):
        match = re.match(r"key_(\d+)_", os.path.basename(path))
        if match:
            codes.add(match.group(1))
    return sorted(codes)


def clean_account_id(name: str) -> str:
    parts = str(name or "").rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 4 and parts[1].isalnum():
        return parts[0]
    return str(name or "")


def decode_v2_bytes(data: bytes, aes_key: bytes, xor_key: int):
    if len(data) < HEADER_SIZE or not data.startswith(MAGIC):
        return None
    signature, aes_size, xor_size = struct.unpack("<6sLLx", data[:HEADER_SIZE])
    if signature != MAGIC:
        return None
    encrypted_size = aes_size + 16 - aes_size % 16
    encrypted = data[HEADER_SIZE:HEADER_SIZE + encrypted_size]
    if len(encrypted) != encrypted_size:
        return None
    try:
        decrypted = Padding.unpad(
            AES.new(aes_key, AES.MODE_ECB).decrypt(encrypted), 16)
    except Exception:
        return None
    remainder = data[HEADER_SIZE + encrypted_size:]
    if xor_size:
        if xor_size > len(remainder):
            return None
        raw = remainder[:-xor_size]
        tail = bytes(value ^ xor_key for value in remainder[-xor_size:])
    else:
        raw, tail = remainder, b""
    return decrypted + raw + tail


def looks_like_video(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[4:8] == b"ftyp":
        return True
    if data[:4] == b"\x1aE\xdf\xa3":  # webm/mkv
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return True
    return False


def decode_v2_file(path: str, out_dir: str, wxid: str, codes: list[str]):
    """Decode one V2 video file; returns the decoded path or None."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if not data.startswith(MAGIC):
        return None
    for code in codes:
        try:
            aes_key = hashlib.md5(f"{code}{wxid}".encode()).hexdigest()[:16].encode()
            output = decode_v2_bytes(data, aes_key, int(code) & 0xFF)
        except Exception:
            continue
        if output and looks_like_video(output):
            os.makedirs(out_dir, exist_ok=True)
            stem = os.path.splitext(os.path.basename(path))[0].replace("_raw", "")
            dest = os.path.join(out_dir, stem + ".mp4")
            with open(dest, "wb") as handle:
                handle.write(output)
            return dest
    return None
