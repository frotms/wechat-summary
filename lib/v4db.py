"""WeChat 4.x (Windows) SQLCipher page decryption + key loading.

Scheme (per chatlog's V4 Windows decryptor):
  passphrase (32 bytes, stored in WeFlow config) --PBKDF2-HMAC-SHA512, iter=256000,
  salt=page1[:16], dkLen=32--> encKey
  macSalt = salt XOR 0x3a; macKey = PBKDF2-HMAC-SHA512(encKey, macSalt, iter=2, dkLen=32)
  page n: HMAC-SHA512(macKey, cipher_part || uint32le(n+1)) stored in last 64 bytes,
          IV in bytes pageSize-reserve .. +16, AES-256-CBC over page[16 or 0 : pageSize-reserve]
  reserve = 80 (16 IV + 64 HMAC), page size = 4096
"""

import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)), 0)
    except ValueError:
        return default


PAGE_SIZE = _env_int("WECHAT_PAGE_SIZE", 4096)
HMAC_SIZE = _env_int("WECHAT_HMAC_SIZE", 64)
IV_SIZE = _env_int("WECHAT_IV_SIZE", 16)
RESERVE = _env_int("WECHAT_RESERVE", 80)
SALT_SIZE = _env_int("WECHAT_SALT_SIZE", 16)
ITER = _env_int("WECHAT_KDF_ITER", 256000)

_CRYPT32 = ctypes.WinDLL("crypt32", use_last_error=True)
_K32 = ctypes.WinDLL("kernel32")


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_unprotect(data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _DATA_BLOB()
    if not _CRYPT32.CryptUnprotectData(
        ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _K32.LocalFree(out.pbData)


def _os_crypt_key(user_data_dir: str) -> bytes:
    ls = json.load(open(os.path.join(user_data_dir, "Local State"), encoding="utf-8"))
    raw = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    if raw[:5] != b"DPAPI":
        raise ValueError("unexpected encrypted_key prefix")
    return _dpapi_unprotect(raw[5:])


def decrypt_safe(value: str, user_data_dir: str) -> str:
    """Decrypt an Electron safeStorage 'safe:' value (v10 AES-GCM or plain DPAPI)."""
    if not value.startswith("safe:"):
        return value
    raw = base64.b64decode(value[5:])
    if raw[:3] == b"v10":
        key = _os_crypt_key(user_data_dir)
        return AESGCM(key).decrypt(raw[3:15], raw[15:], None).decode("utf-8")
    return _dpapi_unprotect(raw).decode("utf-8")


def load_weflow_key(config_path: str, user_data_dir: str) -> bytes:
    cfg = json.load(open(config_path, encoding="utf-8"))
    raw = decrypt_safe(cfg["decryptKey"], user_data_dir)
    raw = raw.strip()
    if len(raw) == 64:
        return bytes.fromhex(raw)
    return raw.encode("utf-8")


def derive_keys(passphrase: bytes, salt: bytes):
    enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, ITER, 32)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, 32)
    return enc_key, mac_key


def _page_mac(mac_key: bytes, body: bytes, page_no: int) -> bytes:
    h = hmac.new(mac_key, digestmod=hashlib.sha512)
    h.update(body)
    h.update((page_no + 1).to_bytes(4, "little"))
    return h.digest()


def validate_db(path: str, passphrase: bytes) -> bool:
    with open(path, "rb") as f:
        page = f.read(PAGE_SIZE)
    if len(page) < PAGE_SIZE:
        return False
    enc_key, mac_key = derive_keys(passphrase, page[:SALT_SIZE])
    body = page[SALT_SIZE : PAGE_SIZE - RESERVE + IV_SIZE]
    return hmac.compare_digest(_page_mac(mac_key, body, 0), page[-HMAC_SIZE:])


def raw_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    """SQLCipher raw-key mode: MAC key = PBKDF2(enc_key, salt^0x3A, 2)."""
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, 32)


def validate_db_raw(path: str, enc_key: bytes, salt: bytes = None) -> bool:
    """Validate a 32-byte SQLCipher raw encryption key (no 256k KDF)."""
    if len(enc_key) not in (16, 32):
        return False
    with open(path, "rb") as f:
        page = f.read(PAGE_SIZE)
    if len(page) < PAGE_SIZE:
        return False
    if salt is None:
        salt = page[:SALT_SIZE]
    if len(salt) != SALT_SIZE:
        return False
    mac_key = raw_mac_key(enc_key, salt)
    body = page[SALT_SIZE : PAGE_SIZE - RESERVE + IV_SIZE]
    return hmac.compare_digest(_page_mac(mac_key, body, 0), page[-HMAC_SIZE:])


def decrypt_db_raw(src: str, dst: str, enc_key: bytes, salt: bytes = None,
                   verify: bool = True) -> None:
    """Decrypt a SQLCipher 4 database with a raw 32-byte encryption key."""
    with open(src, "rb") as f:
        page1 = f.read(PAGE_SIZE)
    if len(page1) < PAGE_SIZE:
        raise ValueError(f"{src}: file shorter than one page")
    if salt is None:
        salt = page1[:SALT_SIZE]
    mac_key = raw_mac_key(enc_key, salt)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".tmp"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        fout.write(b"SQLite format 3\x00")
        page_no = 0
        while True:
            page = fin.read(PAGE_SIZE)
            if not page:
                break
            if len(page) < PAGE_SIZE:
                page = page + b"\x00" * (PAGE_SIZE - len(page))
            if page == b"\x00" * PAGE_SIZE:
                fout.write(page)
                page_no += 1
                continue
            offset = SALT_SIZE if page_no == 0 else 0
            data_end = PAGE_SIZE - RESERVE + IV_SIZE
            if verify:
                calc = _page_mac(mac_key, page[offset:data_end], page_no)
                stored = page[data_end : data_end + HMAC_SIZE]
                if not hmac.compare_digest(calc, stored):
                    raise ValueError(f"{src}: HMAC mismatch on page {page_no + 1}")
            iv = page[data_end - IV_SIZE : data_end]
            body = page[offset : PAGE_SIZE - RESERVE]
            dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
            plain = dec.update(body) + dec.finalize()
            fout.write(plain + page[PAGE_SIZE - RESERVE :])
            page_no += 1
    os.replace(tmp, dst)


def decrypt_db(src: str, dst: str, passphrase: bytes, verify: bool = True) -> None:
    with open(src, "rb") as f:
        page1 = f.read(PAGE_SIZE)
    if len(page1) < PAGE_SIZE:
        raise ValueError(f"{src}: file shorter than one page")
    salt = page1[:SALT_SIZE]
    enc_key, mac_key = derive_keys(passphrase, salt)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".tmp"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        fout.write(b"SQLite format 3\x00")
        page_no = 0
        while True:
            page = fin.read(PAGE_SIZE)
            if not page:
                break
            if len(page) < PAGE_SIZE:
                page = page + b"\x00" * (PAGE_SIZE - len(page))
            if page == b"\x00" * PAGE_SIZE:
                fout.write(page)
                page_no += 1
                continue
            offset = SALT_SIZE if page_no == 0 else 0
            data_end = PAGE_SIZE - RESERVE + IV_SIZE
            if verify:
                calc = _page_mac(mac_key, page[offset:data_end], page_no)
                stored = page[data_end : data_end + HMAC_SIZE]
                if not hmac.compare_digest(calc, stored):
                    raise ValueError(f"{src}: HMAC mismatch on page {page_no + 1}")
            iv = page[data_end - IV_SIZE : data_end]
            body = page[offset : PAGE_SIZE - RESERVE]
            dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
            plain = dec.update(body) + dec.finalize()
            fout.write(plain + page[PAGE_SIZE - RESERVE :])
            page_no += 1
    os.replace(tmp, dst)
