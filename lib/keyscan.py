#!/usr/bin/env python
"""Read-only WeChat 4.x master-key locator (Windows).

Scans the running Weixin.exe process memory for the WCDB config object
``com.Tencent.WCDB.Config.Cipher``, unwraps its XOR-masked value, extracts
``x'<64hex>'`` key material, and validates candidates against a database copy
via the SQLCipher 4 HMAC in ``v4db``.

The key is never printed or written to disk.  ``find_master_key()`` returns it
to the caller so an export can run in the same process.

This only uses OpenProcess/ReadProcessMemory/VirtualQueryEx.  It does not
inject, hook, patch, or modify WeChat.
"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from . import v4db
except ImportError:  # direct script execution
    import v4db  # noqa: E402

CONFIG_CIPHER_NAME = b"com.Tencent.WCDB.Config.Cipher"
CONFIG_XOR_MASK = bytes.fromhex(
    os.environ.get("WECHAT_CONFIG_XOR_MASK",
                   "d2c7442458020000004889442450488b"
                   "450048844c2448488944254048584c24")
)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)), 0)
    except ValueError:
        return default


CFG_PTR_BACK = _env_int("WECHAT_CFG_BACK", 0x138)
CFG_OFFSET = _env_int("WECHAT_CFG_OFFSET", 0x68)
CFG_DWORD_OFF = _env_int("WECHAT_CFG_DWORD_OFF", 0x40)
HEX_LITERAL_RE = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")

PAGE = 4096
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.VirtualQueryEx.restype = ctypes.c_size_t


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wt.DWORD), ("Protect", wt.DWORD), ("Type", wt.DWORD)]


def _pids():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="replace", timeout=20,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "weixin.exe" and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def _regions(h):
    out, addr, mbi = [], 0, MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base, size = int(mbi.BaseAddress or 0), int(mbi.RegionSize or 0)
        if size <= 0:
            break
        if (mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) in READABLE
                and not (mbi.Protect & 0x100)):
            out.append((base, size))
        addr = base + size
    return out


def _read(h, addr, size):
    if addr < 0x10000 or addr > 0x7FFFFFFFFFFF or size <= 0:
        return None
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def _find_all(h, needles, chunk=4 * 1024 * 1024):
    """One pass over readable memory; returns {needle: [absolute addresses]}."""
    found = {n: [] for n in needles}
    needle_list = list(needles)
    for base, size in _regions(h):
        off = 0
        while off < size:
            take = min(chunk, size - off)
            data = _read(h, base + off, take)
            if data:
                for n in needle_list:
                    i = data.find(n)
                    while i != -1:
                        found[n].append(base + off + i)
                        i = data.find(n, i + 1)
            off += take
    return found


def _probable_key(b):
    return (len(b) == 32 and len(set(b)) >= 15
            and b != b"\x00" * 32 and b != b"\xff" * 32)


def _extract_candidates(h):
    needles = _find_all(h, [CONFIG_CIPHER_NAME]).get(CONFIG_CIPHER_NAME, [])
    if not needles:
        return set()

    pairs = {
        struct.pack("<Q", addr) + struct.pack("<Q", len(CONFIG_CIPHER_NAME)): addr
        for addr in needles
    }
    pair_hits = _find_all(h, list(pairs))
    candidates = set()
    for pair, needle_addr in pairs.items():
        for qaddr in pair_hits.get(pair, []):
            node = _read(h, qaddr - 0x10, 0x50)
            if not node or len(node) < 0x40:
                continue
            if struct.unpack_from("<Q", node, 0x10)[0] != needle_addr:
                continue
            if struct.unpack_from("<Q", node, 0x18)[0] != len(CONFIG_CIPHER_NAME):
                continue
            config_ptr = struct.unpack_from("<Q", node, 0x28)[0]
            if not (0x10000 <= config_ptr < 0x800000000000):
                continue
            obj = _read(h, config_ptr + 0x88, 0x28)
            if not obj or len(obj) < 0x18:
                continue
            data_ptr = struct.unpack_from("<Q", obj, 0x8)[0]
            data_len = struct.unpack_from("<Q", obj, 0x10)[0]
            if not (0 < data_len <= 1024 and 0x10000 <= data_ptr < 0x800000000000):
                continue
            blob = _read(h, data_ptr, int(data_len))
            if not blob or len(blob) != data_len:
                continue
            decoded = bytes(v ^ CONFIG_XOR_MASK[i % len(CONFIG_XOR_MASK)]
                            for i, v in enumerate(blob))
            for m in HEX_LITERAL_RE.finditer(decoded):
                run = m.group(1).decode().lower()
                starts = [0]
                if len(run) > 96:
                    starts += list(range(0, len(run) - 63, 32))
                    starts.append(len(run) - 64)
                for s in dict.fromkeys(starts):
                    if s + 64 > len(run):
                        continue
                    cand = bytes.fromhex(run[s:s + 64])
                    if _probable_key(cand):
                        candidates.add(cand)
    return candidates


def find_master_key(account_dir, db_rel="session/session.db", verbose=False):
    """Return the 32-byte master key for ``account_dir`` or None.

    The key is validated against ``db_storage/<db_rel>`` and never logged.
    """
    db = os.path.join(account_dir, "db_storage", db_rel)
    if not os.path.isfile(db):
        return None
    cands = set()
    for pid in _pids():
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            continue
        try:
            cands |= _extract_candidates(h)
        except Exception:
            pass
        finally:
            k32.CloseHandle(h)
    if verbose:
        print(f"candidates from memory: {len(cands)}", file=sys.stderr)
    for cand in cands:
        try:
            if v4db.validate_db(db, cand):
                if verbose:
                    print("master key validated against session.db", file=sys.stderr)
                return cand
        except Exception:
            continue
    return None


def collect_raw_keys(account_dir, verbose=False):
    """Return {relative_db_path: 32-byte raw SQLCipher key} for one account.

    WeChat 4.x stores one raw key per database in the WCDB config object.
    Keys are only returned to the caller; nothing is printed or written.
    """
    cands = set()
    for pid in _pids():
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            continue
        try:
            cands |= _extract_candidates(h)
        except Exception:
            pass
        finally:
            k32.CloseHandle(h)
    cands = list(cands)
    if verbose:
        print(f"raw key candidates: {len(cands)}", file=sys.stderr)
    keys = {}
    root = os.path.join(account_dir, "db_storage")
    if not cands or not os.path.isdir(root):
        return keys
    for rel in _db_rel_paths(root):
        path = os.path.join(root, rel.replace("/", os.sep))
        try:
            with open(path, "rb") as f:
                page = f.read(v4db.PAGE_SIZE)
        except OSError:
            continue
        if len(page) < v4db.PAGE_SIZE:
            continue
        for cand in cands:
            try:
                if v4db.validate_db_raw(path, cand):
                    keys[rel] = cand
                    break
            except Exception:
                continue
    if verbose:
        print(f"databases with keys: {len(keys)}", file=sys.stderr)
    return keys


def _db_rel_paths(root):
    out = []
    for r, dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".db"):
                rel = os.path.relpath(os.path.join(r, fn), root).replace(os.sep, "/")
                out.append(rel)
    return sorted(out)


def weixin_info():
    """Return non-secret diagnostics about the running WeChat installation."""
    import hashlib
    info = {"pids": _pids(), "exe": None, "version": None, "dll": None,
            "dll_sha256": None, "dll_size": None}
    if not info["pids"]:
        return info
    pid = info["pids"][0]
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wt.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                info["exe"] = buf.value
        finally:
            k32.CloseHandle(h)
    exe = info["exe"]
    if exe and os.path.isfile(exe):
        try:
            ver = ctypes.WinDLL("version")
            size = ver.GetFileVersionInfoSizeW(exe, None)
            if size:
                vb = ctypes.create_string_buffer(size)
                ver.GetFileVersionInfoW(exe, 0, size, vb)
                r = ctypes.c_void_p()
                ln = wt.UINT()
                ver.VerQueryValueW(vb, "\\", ctypes.byref(r), ctypes.byref(ln))
                if r.value:
                    class _FI(ctypes.Structure):
                        _fields_ = [("dwSignature", wt.DWORD), ("dwStrucVersion", wt.DWORD),
                                    ("dwFileVersionMS", wt.DWORD), ("dwFileVersionLS", wt.DWORD),
                                    ("dwProductVersionMS", wt.DWORD), ("dwProductVersionLS", wt.DWORD),
                                    ("dwFileFlagsMask", wt.DWORD), ("dwFileFlags", wt.DWORD),
                                    ("dwFileOS", wt.DWORD), ("dwFileType", wt.DWORD),
                                    ("dwFileSubtype", wt.DWORD), ("dwFileDateMS", wt.DWORD),
                                    ("dwFileDateLS", wt.DWORD)]
                    fi = _FI.from_address(r.value)
                    info["version"] = "%d.%d.%d.%d" % (
                        fi.dwFileVersionMS >> 16, fi.dwFileVersionMS & 0xFFFF,
                        fi.dwFileVersionLS >> 16, fi.dwFileVersionLS & 0xFFFF)
        except Exception:
            pass
    try:
        got = _weixin_dll(pid)
        if got:
            hh, _base, dll_path = got
            k32.CloseHandle(hh)
            info["dll"] = dll_path
            if os.path.isfile(dll_path):
                info["dll_size"] = os.path.getsize(dll_path)
                sha = hashlib.sha256()
                with open(dll_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
                        sha.update(chunk)
                info["dll_sha256"] = sha.hexdigest()
    except Exception:
        pass
    return info


# ---------------------------------------------------------------- image key

IMAGE_V2_MAGIC = b"\x07\x08\x56\x32\x08\x07"
IMAGE_V1_MAGIC = b"\x07\x08\x05\x56\x02\x05"
HEX16_RE = re.compile(rb"(?<![0-9a-zA-Z])([0-9a-fA-F]{16})(?![0-9a-zA-Z])")
ALNUM32_RE = re.compile(rb"(?<![0-9a-zA-Z])([0-9a-zA-Z]{32})(?![0-9a-zA-Z])")


def _image_like(pt):
    return (pt[:3] == b"\xff\xd8\xff"
            or pt[:4] in (b"\x89PNG", b"GIF8", b"RIFF", b"wxgf"))


def _image_probes(account_dir, limit=4):
    probes = []
    root = os.path.join(account_dir, "msg", "attach")
    if not os.path.isdir(root):
        return probes
    for r, ds, fs in os.walk(root):
        if os.path.basename(r) != "Img":
            continue
        for fn in fs:
            if not fn.endswith(".dat") or fn.endswith(("_t.dat", "_h.dat")):
                continue
            p = os.path.join(r, fn)
            try:
                with open(p, "rb") as f:
                    head = f.read(32)
            except OSError:
                continue
            if head[:6] == IMAGE_V2_MAGIC and len(head) >= 31:
                probes.append((p, head[15:31]))
                if len(probes) >= limit:
                    return probes
    return probes


def _aes_probe_ok(key, probes):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    try:
        enc = key.encode("ascii")
    except Exception:
        return False
    if len(enc) != 16:
        return False
    try:
        for _p, probe in probes:
            dec = Cipher(algorithms.AES(enc), modes.ECB()).decryptor().update(probe)
            if not _image_like(dec):
                return False
        return True
    except Exception:
        return False


def _weixin_dll(pid):
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EnumProcessModules.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE),
                                         wt.DWORD, ctypes.POINTER(wt.DWORD)]
    psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return None
    mods = (wt.HMODULE * 1024)()
    needed = wt.DWORD()
    if not psapi.EnumProcessModules(h, mods, ctypes.sizeof(mods), ctypes.byref(needed)):
        k32.CloseHandle(h)
        return None
    for i in range(needed.value // ctypes.sizeof(wt.HMODULE)):
        buf = ctypes.create_unicode_buffer(1024)
        if psapi.GetModuleFileNameExW(h, mods[i], buf, 1024):
            if buf.value.lower().endswith("weixin.dll"):
                return h, int(mods[i]), buf.value
    k32.CloseHandle(h)
    return None


def _image_wxid_candidates(account_dir):
    base = os.path.basename(account_dir.rstrip("/\\"))
    out = [base]
    if "_" in base:
        stem, tail = base.rsplit("_", 1)
        if len(tail) == 4 and re.fullmatch(r"[0-9a-fA-F]{4}", tail):
            out.append(stem)
    try:
        cfg = os.path.join(os.environ.get("APPDATA", ""), "weflow", "WeFlow-config.json")
        if os.path.isfile(cfg):
            import json
            d = json.load(open(cfg, encoding="utf-8"))
            for k in ("myWxid", "myAccountId"):
                if d.get(k):
                    out.append(str(d[k]))
    except Exception:
        pass
    return list(dict.fromkeys(out))


def _derive_aes(cfg_dword, wxids):
    for wx in wxids:
        yield wx, hashlib.md5((str(cfg_dword) + wx).encode("utf-8")).hexdigest()[:16]


def _find_cfg_dword(account_dir):
    """Locate cfgDword in the weixin.dll cfg object (read-only pointer chain)."""
    probes = _image_probes(account_dir)
    if not probes:
        return None
    wxids = _image_wxid_candidates(account_dir)
    for pid in _pids():
        got = _weixin_dll(pid)
        if not got:
            continue
        h, base, _path = got
        try:
            hdr = _read(h, base, 0x1000)
            if not hdr:
                continue
            e_lfanew = struct.unpack_from("<I", hdr, 0x3C)[0]
            image_size = struct.unpack_from("<I", hdr, e_lfanew + 0x18 + 56)[0]
            if not (0 < image_size < 0x80000000):
                continue
            positions = []
            step = 4 * 1024 * 1024
            off = 0
            while off < image_size:
                chunk = _read(h, base + off, min(step, image_size - off))
                if chunk:
                    i = chunk.find(b"global_config")
                    while i != -1:
                        pos = off + i + 16  # string at pos-16, size at pos, cap at pos+8
                        sz = _read(h, base + pos, 4)
                        cap = _read(h, base + pos + 8, 4)
                        if (sz and cap and struct.unpack("<I", sz)[0] == 13
                                and (struct.unpack("<I", cap)[0] | 0xF) == 0xF):
                            positions.append(pos)
                        i = chunk.find(b"global_config", i + 1)
                off += step
            if not positions:
                continue
            # Known offsets first (4.1.10/4.1.13), then a bounded brute force.
            chains = [(CFG_PTR_BACK, CFG_OFFSET), (0x130, 0x68)]
            for back in range(0x100, 0x1A0, 8):
                for coff in range(0x40, 0x100, 8):
                    if (back, coff) not in chains:
                        chains.append((back, coff))
            for pos in positions:
                for back, coff in chains:
                    vb = _read(h, base + pos - back, 8)
                    if not vb:
                        continue
                    v18 = struct.unpack("<Q", vb)[0]
                    if not (0x10000 <= v18 < 0x800000000000):
                        continue
                    cb = _read(h, v18 + coff, 8)
                    if not cb:
                        continue
                    cfg = struct.unpack("<Q", cb)[0]
                    if not (0x10000 <= cfg < 0x800000000000):
                        continue
                    for doff in (CFG_DWORD_OFF, 0x38, 0x48, 0x30, 0x50, 0x58):
                        db = _read(h, cfg + doff, 4)
                        if not db:
                            continue
                        dword = struct.unpack("<I", db)[0]
                        if not dword:
                            continue
                        for wx, aes in _derive_aes(dword, wxids):
                            if _aes_probe_ok(aes, probes):
                                return aes, dword & 0xFF
        finally:
            k32.CloseHandle(h)
    return None


def find_image_key(account_dir, verbose=False, allow_scan=True):
    """Return (aes_key, xor_key) for image .dat files, or None.

    First derives cfgDword from the weixin.dll cfg object (fast and works even
    before an image is opened); optionally falls back to scanning memory for a
    16-char AES key.  Nothing is printed or persisted.
    """
    derived = _find_cfg_dword(account_dir)
    if derived:
        if verbose:
            print("image key derived from cfgDword", file=sys.stderr)
        return derived
    if not allow_scan:
        return None

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    probes = _image_probes(account_dir)
    if not probes:
        return None
    cands = set()
    for pid in _pids():
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            continue
        try:
            for base, size in _regions(h):
                off = 0
                while off < size:
                    take = min(4 * 1024 * 1024, size - off)
                    data = _read(h, base + off, take)
                    if data:
                        for m in HEX16_RE.finditer(data):
                            s = m.group(1).lower()
                            if len(set(s)) >= 8:
                                cands.add(s.decode("ascii"))
                        for m in ALNUM32_RE.finditer(data):
                            cands.add(m.group(1)[:16].decode("ascii", "replace"))
                    off += take
        finally:
            k32.CloseHandle(h)
    if verbose:
        print(f"image key candidates: {len(cands)}", file=sys.stderr)

    def aes_ok(key):
        try:
            enc = key.encode("ascii")
            if len(enc) != 16:
                return False
            for _p, probe in probes:
                dec = Cipher(algorithms.AES(enc), modes.ECB()).decryptor().update(probe)
                if not _image_like(dec):
                    return False
            return True
        except Exception:
            return False

    aes_key = next((k for k in cands if aes_ok(k)), None)
    if not aes_key:
        return None

    # Find XOR byte using the actual V2 layout.
    path, _probe = probes[0]
    with open(path, "rb") as f:
        data = f.read()
    aes_size, xor_size = struct.unpack_from("<LL", data, 6)
    aes_blk = aes_size + (16 - aes_size % 16) if aes_size % 16 else aes_size + 16
    off = 15
    aes_data = data[off:off + aes_blk]
    off += aes_blk
    raw = data[off:len(data) - xor_size]
    enc_tail = data[len(data) - xor_size:]
    dec = Cipher(algorithms.AES(aes_key.encode("ascii")), modes.ECB()).decryptor()
    pt = dec.update(aes_data) + dec.finalize()
    pad = pt[-1] if pt else 0
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        pt = pt[:-pad]
    for xor in range(256):
        out = pt + raw + bytes(b ^ xor for b in enc_tail)
        if out[:3] == b"\xff\xd8\xff" and b"\xff\xd9" in out[-64:]:
            return aes_key, xor
        if out[:8] == b"\x89PNG\r\n\x1a\n" and b"IEND" in out[-64:]:
            return aes_key, xor
    return None


def main():
    if len(sys.argv) < 2:
        print("usage: python keyscan.py <account_dir> [db_rel]", file=sys.stderr)
        return 2
    acct = os.path.abspath(sys.argv[1])
    rel = sys.argv[2] if len(sys.argv) > 2 else "session/session.db"
    raw = collect_raw_keys(acct, verbose=True)
    if raw:
        print(f"FOUND raw keys for {len(raw)} databases (not printed)")
        return 0
    key = find_master_key(acct, rel, verbose=True)
    if key:
        print("FOUND master key (not printed)")
        return 0
    print("NOT FOUND")
    return 1


if __name__ == "__main__":
    sys.exit(main())
