# -*- coding: utf-8 -*-
"""WeChat custom stickers (表情包): decrypt the local cache and decode them.

WeChat 4.x keeps custom stickers in

    <account>/business/emoticon/{Persist,Thumb,Temp}/<md5[:2]>/<md5>
    <account>/cache/<YYYY-MM>/Emoticon/<md5[:2]>/<md5>

Each file is AES-128-CBC encrypted with the key used as its own IV, where

    key = md5(f"{seed}{wxid}EMOTICON")[:16]

`seed` is a per-account constant that only exists in WeChat's process memory,
so it has to be scanned for once (see `find_seed`). Decoded payloads are
either a plain image (PNG/JPEG/GIF) or WeChat's `wxgf` container - a small
header followed by a raw H.265 stream - which needs ffmpeg to become an image.

Reference: CN-Grace/Wechat-Emoticon-Parser (v4.0-plus branch).
"""
from __future__ import annotations

import ctypes
import glob
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from ctypes import wintypes

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _HAVE_CRYPTO = True
except ImportError:  # pragma: no cover - dependency is declared in the docs
    _HAVE_CRYPTO = False

#: Magic bytes we recognise after decryption.
IMAGE_MAGICS = (
    (b"\x89PNG", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF8", "image/gif", ".gif"),
    (b"RIFF", "image/webp", ".webp"),
    (b"wxgf", "image/hevc", ".hevc"),
)

#: WeChat's own sticker container: a header followed by a raw H.265 stream.
WXGF_MAGIC = b"wxgf"

#: The sticker seed is a decimal string of this length range (observed: 9).
SEED_RE = re.compile(rb"\d{5,20}")

#: Upper bound on how many memory candidates we try (each one is a decrypt).
SEED_MAX_CANDIDATES = 200_000


def derive_key(seed: str, wxid: str) -> bytes:
    """Cache key for one account. Returns 16 bytes."""
    return hashlib.md5(f"{seed}{wxid}EMOTICON".encode()).digest()[:16]


def account_wxid(account_dir_name: str) -> str:
    """`wxid_账号A_后缀` -> `wxid_账号A`."""
    parts = str(account_dir_name or "").rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 4:
        return parts[0]
    return str(account_dir_name or "")


def decrypt(raw: bytes, key: bytes) -> bytes:
    """AES-128-CBC(key=IV) with PKCS7. Returns plaintext (best effort)."""
    if not _HAVE_CRYPTO or not raw or len(raw) < 32:
        return b""
    body = raw[: len(raw) // 16 * 16]
    dec = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    out = dec.update(body) + dec.finalize()
    try:
        unp = padding.PKCS7(128).unpadder()
        return unp.update(out) + unp.finalize()
    except Exception:
        return out


def sniff(data: bytes):
    """(mime, extension) for decrypted bytes, or (None, None)."""
    for magic, mime, ext in IMAGE_MAGICS:
        if data.startswith(magic):
            return mime, ext
    return None, None


def sticker_cache_dirs(account_root: str):
    """Where WeChat stores cached stickers for this account."""
    dirs = []
    cache_root = os.path.join(account_root, "cache")
    if os.path.isdir(cache_root):
        for month in sorted(os.listdir(cache_root)):
            d = os.path.join(cache_root, month, "Emoticon")
            if os.path.isdir(d):
                dirs.append(d)
    for sub in ("Persist", "Thumb", "Temp"):
        d = os.path.join(account_root, "business", "emoticon", sub)
        if os.path.isdir(d):
            dirs.append(d)
    return dirs


def find_sticker_file(dirs, md5_hex: str):
    """First cached file for `md5_hex`, or ''.

    The `Thumb` variant (`<md5>.thumb`) is a smaller PNG of the same sticker,
    so it is only used when nothing else matches.
    """
    md5_hex = (md5_hex or "").lower()
    if not md5_hex:
        return ""
    for base in dirs:
        candidate = os.path.join(base, md5_hex[:2], md5_hex)
        if os.path.isfile(candidate):
            return candidate
    for base in dirs:
        candidate = os.path.join(base, md5_hex[:2], md5_hex + ".thumb")
        if os.path.isfile(candidate):
            return candidate
    return ""


def any_sticker_file(dirs):
    """Some cached sticker, used as the verification sample."""
    for base in dirs:
        for sub in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            path = os.path.join(base, sub)
            if not os.path.isdir(path):
                continue
            for name in sorted(os.listdir(path)):
                f = os.path.join(path, name)
                if os.path.isfile(f) and os.path.getsize(f) > 32:
                    return f
    return ""


# --------------------------------------------------------------------------
# seed discovery
# --------------------------------------------------------------------------

class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _wechat_pid() -> int:
    """PID of the running WeChat client, or 0."""
    try:
        import pymem
    except ImportError:
        return 0
    for proc in pymem.process.list_processes():
        try:
            name = proc.szExeFile
            if isinstance(name, bytes):
                name = name.decode("utf-8", "ignore")
            if str(name).lower() in ("weixin.exe", "wechat.exe"):
                return proc.th32ProcessID
        except Exception:
            continue
    return 0


def seed_candidates():
    """Decimal strings scraped from WeChat's process memory."""
    if os.name != "nt":
        return []
    pid = _wechat_pid()
    if not pid:
        return []

    k32 = ctypes.windll.kernel32
    rpm = k32.ReadProcessMemory
    rpm.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    vqe = k32.VirtualQueryEx
    vqe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
    vqe.restype = ctypes.c_size_t

    handle = k32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not handle:
        return []

    seen = set()
    addr = 0x10000
    try:
        while addr < 0x7FFFFFFFFFFF:
            mbi = _MBI()
            if vqe(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            base, size = mbi.BaseAddress or 0, mbi.RegionSize or 0
            if size == 0:
                break
            if (mbi.State == 0x1000 and 256 < size < 200 * 1024 * 1024
                    and mbi.Protect not in (0, 0x01, 0x100)):
                pos, end = base, base + size
                while pos < end:
                    n = min(65536, end - pos)
                    buf = ctypes.create_string_buffer(n)
                    read = ctypes.c_size_t(0)
                    if rpm(handle, ctypes.c_void_p(pos), buf, n, ctypes.byref(read)) and read.value:
                        for match in SEED_RE.finditer(buf.raw[: read.value]):
                            candidate = match.group(0).decode()
                            if candidate not in seen:
                                seen.add(candidate)
                                yield candidate
                                if len(seen) >= SEED_MAX_CANDIDATES:
                                    return
                    pos += 65536
            addr = base + size
    finally:
        k32.CloseHandle(handle)


def find_seed(wxid: str, sample_file: str, candidates=None):
    """The seed that decrypts `sample_file`, or None.

    Every candidate is proven by decrypting the sample and looking for a
    known image magic, so a false positive is essentially impossible.
    """
    if not sample_file:
        return None
    try:
        with open(sample_file, "rb") as handle:
            raw = handle.read(64)
    except OSError:
        return None
    if not raw:
        return None
    for seed in (candidates if candidates is not None else seed_candidates()):
        mime, _ = sniff(decrypt(raw, derive_key(seed, wxid)))
        if mime and mime != "image/hevc":
            return seed
        if mime == "image/hevc":
            return seed
    return None


# --------------------------------------------------------------------------
# wxgf decoding
# --------------------------------------------------------------------------

def ffmpeg_exe():
    """An ffmpeg binary: the bundled `imageio-ffmpeg` one, or one on PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def decode_wxgf(data: bytes, out_dir: str, stem: str):
    """Decode a `wxgf` payload into an image file.

    Multi-frame payloads (animated stickers) become an animated GIF; a single
    frame becomes a PNG. Returns (path, mime) or ('', '').
    """
    if not data.startswith(WXGF_MAGIC):
        return "", ""
    ffmpeg = ffmpeg_exe()
    if not ffmpeg:
        return "", ""
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="wechat-sticker-")
    try:
        src = os.path.join(tmp_dir, "sticker.hevc")
        with open(src, "wb") as handle:
            handle.write(data[4:])
        frames_dir = os.path.join(tmp_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "hevc", "-i", src,
             "-fps_mode", "passthrough", os.path.join(frames_dir, "f%04d.png")],
            capture_output=True, timeout=180,
        )
        frames = sorted(glob.glob(os.path.join(frames_dir, "f*.png")))
        if not frames:
            return "", ""
        if len(frames) == 1:
            out_path = os.path.join(out_dir, f"{stem}.png")
            shutil.copyfile(frames[0], out_path)
            return out_path, "image/png"
        out_path = os.path.join(out_dir, f"{stem}.gif")
        gif = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "hevc", "-i", src,
             "-vf", ("fps=10,scale=240:-1:flags=lanczos,split[a][b];"
                     "[a]palettegen[p];[b][p]paletteuse"),
             "-loop", "0", out_path],
            capture_output=True, timeout=180,
        )
        if gif.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            return "", ""
        return out_path, "image/gif"
    except Exception:
        return "", ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def prepare_sticker(dirs, md5_hex: str, key: bytes, out_dir: str):
    """Decrypt (and decode) one sticker. Returns (path, mime) or ('', '').

    Decoding a `wxgf` payload costs about a second, so an already-decoded
    file in `out_dir` is reused instead of being decoded again.
    """
    md5_hex = (md5_hex or "").lower()
    if not md5_hex:
        return "", ""
    for cached in (f"{md5_hex}.png", f"{md5_hex}.gif", f"{md5_hex}.jpg", f"{md5_hex}.webp"):
        candidate = os.path.join(out_dir, cached)
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            try:
                with open(candidate, "rb") as handle:
                    mime, _ = sniff(handle.read(16))
            except OSError:
                continue
            return candidate, mime or "image/png"
    src = find_sticker_file(dirs, md5_hex)
    if not src:
        return "", ""
    try:
        with open(src, "rb") as handle:
            raw = handle.read()
    except OSError:
        return "", ""

    mime, ext = sniff(raw)
    data = raw
    if not mime:
        data = decrypt(raw, key)
        mime, ext = sniff(data)
        if not mime:
            return "", ""

    os.makedirs(out_dir, exist_ok=True)
    if mime == "image/hevc":
        return decode_wxgf(data, out_dir, md5_hex)
    out_path = os.path.join(out_dir, f"{md5_hex}{ext}")
    try:
        with open(out_path, "wb") as handle:
            handle.write(data)
    except OSError:
        return "", ""
    return out_path, mime
