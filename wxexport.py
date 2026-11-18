#!/usr/bin/env python
"""WeChat 4.x chat exporter: text + images + videos + files, with filters.

Used by wfchat.py's ``export`` command.  Read-only: WeChat's own directory is
never modified; decrypted databases and copied media live under the chosen
output directory (default ``$WECHAT_WORK_DIR/export_*``).

Media facts for WeChat 4.x on Windows (verified on this machine):
  * images:  msg/attach/<dir1>/<dir2>/Img/<md5>.dat   (v2: AES-128-ECB + XOR)
  * videos:  msg/video/<YYYY-MM>/<md5>.mp4            (plaintext)
  * files:   msg/file/<YYYY-MM>/<original name>       (plaintext)
  * voice:   message/media_0.db VoiceInfo.voice_data  (SILK bytes, best effort)
  * hardlink.db maps md5 -> file name + dir ids for images/videos/files.
"""

import collections
import datetime
import glob
import hashlib
import html
import json
import os
import re
import shutil
import struct
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from lib import keyscan as keyscan_v4
from lib import silk as silkdec
from lib import v4db
from lib import wxdata as ec

V2_MAGIC = b"\x07\x08\x56\x32\x08\x07"
V1_MAGIC = b"\x07\x08\x05\x56\x02\x05"
WXGF_MAGIC = b"\x41\x98\x86\x8f"
MEDIA_KINDS = {"image", "video", "voice", "file", "sticker", "app"}
EXPORT_KINDS = ("text", "image", "video", "voice", "file", "sticker", "app",
                "system", "card", "location", "call", "unknown")
_FOOTER_MAX = 32
_IMAGE_INDEX_CACHE = {}
_CACHE_INDEX_CACHE = {}
_HARDLINK_MEM_CACHE = {}
_IMAGE_KEY_CACHE = {}
_STICKER_INDEX_CACHE = {}


# ---------------------------------------------------------------- utilities

def _safe_name(text, maxlen=100):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text or "")).strip(" .")
    return (text or "untitled")[:maxlen]


def _fmt_size(size):
    try:
        size = int(size)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024.0
    return ""


def _now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_when(text, until=False):
    """Parse YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS] / ISO into a timestamp."""
    if not text:
        return None
    text = str(text).strip()
    t = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            t = datetime.datetime.strptime(text, fmt)
            break
        except ValueError:
            pass
    if t is None:
        try:
            t = datetime.datetime.fromisoformat(text)
        except ValueError:
            raise SystemExit(f"无法解析时间: {text}（用 YYYY-MM-DD 或 'YYYY-MM-DD HH:MM'）")
    if until and len(text) == 10:
        t += datetime.timedelta(days=1)
    return int(t.timestamp())


def _fmt_time(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return str(ts)


def _short_md5(md5):
    return (md5 or "")[:8]


def _unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}_{n}{ext}"):
        n += 1
    return f"{stem}_{n}{ext}"


# ---------------------------------------------------------------- filters

def filter_messages(messages, args):
    lo = parse_when(getattr(args, "since", None))
    hi = parse_when(getattr(args, "until", None), until=True)
    types = {t.strip() for t in (getattr(args, "types", "") or "").split(",") if t.strip()}
    kw = (getattr(args, "keyword", None) or "").strip().lower()
    sender = (getattr(args, "sender", None) or "").strip().lower()
    out = []
    for m in messages:
        if lo is not None and m["time"] < lo:
            continue
        if hi is not None and m["time"] >= hi:
            continue
        if types:
            kind = m["kind"]
            ok = kind in types
            # "app" historically covered link/card/file app messages.
            if not ok and "app" in types and kind in ("file", "card", "location", "call"):
                ok = True
            # "file" should also catch documents sent as appmsg type 6.
            if not ok and "file" in types and kind == "app" and (
                    m.get("extra") or {}).get("app_type") == "6":
                ok = True
            if not ok and str(m["type"]) in types:
                ok = True
            if not ok:
                continue
        if kw and kw not in (m["text"] or "").lower():
            continue
        if sender:
            names = [(m.get("sender_name") or ""), (m.get("sender") or "")]
            if m.get("is_self"):
                names.append("我")
            if not any(sender in n.lower() for n in names if n):
                continue
        out.append(m)
    # Keep --last meaning "the newest N" even if the caller passed messages in
    # a different order.
    out.sort(key=lambda m: m["time"])
    last = getattr(args, "last", None)
    if last:
        out = out[-last:]
    return out


# ---------------------------------------------------------------- media

class ImageKeyMissing(Exception):
    pass


def _read_probe(account_dir):
    """Take the first V2 ciphertext block as a key-validation probe."""
    for p in glob.glob(os.path.join(account_dir, "msg", "attach", "*", "*", "Img", "*.dat"))[:400]:
        try:
            with open(p, "rb") as f:
                head = f.read(40)
        except OSError:
            continue
        if head[:6] == V2_MAGIC and len(head) >= 31:
            return head[15:31], "v2", p
        if head[:6] == V1_MAGIC and len(head) >= 38:
            return head[22:38], "v1", p
    return b"", "", None


def _valid_aes(probe, aes_key):
    if not probe or not aes_key:
        return False
    try:
        dec = Cipher(algorithms.AES(aes_key.encode("ascii")), modes.ECB()).decryptor()
        pt = dec.update(probe) + dec.finalize()
    except Exception:
        return False
    return pt[:3] == b"\xff\xd8\xff" or pt[:4] in (b"\x89PNG", b"GIF8", b"RIFF", b"wxgf")


def _derive_image_keys(account_dir, own_username):
    """Resolve image xor/aes keys without ever printing them.

    Order: keys.json -> WeFlow config safeStorage -> cfg_dword derivation.
    """
    cached = _IMAGE_KEY_CACHE.get(account_dir)
    if cached is not None:
        return cached
    keys = {}
    try:
        keys = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.json"),
                              encoding="utf-8"))
    except Exception:
        keys = {}
    xor_key = keys.get("image_xor")
    aes_key = keys.get("image_aes")
    cfg_dword = keys.get("cfg_dword")
    source = []
    if xor_key is not None:
        source.append("keys.json:xor")
    if aes_key:
        source.append("keys.json:aes")

    cfg = {}
    if os.path.isfile(ec.WEFLOW_CONFIG):
        try:
            cfg = json.load(open(ec.WEFLOW_CONFIG, encoding="utf-8"))
        except Exception:
            cfg = {}
    acct_cfg = (cfg.get("wxidConfigs") or {}).get(os.path.basename(account_dir), {}) or {}

    def _dec(value):
        if not value:
            return ""
        try:
            return v4db.decrypt_safe(value, ec.WEFLOW_USERDATA).strip()
        except Exception:
            return str(value).strip()

    if xor_key is None:
        raw_xor = _dec(acct_cfg.get("imageXorKey") or cfg.get("imageXorKey"))
        if raw_xor:
            try:
                xor_key = int(raw_xor, 0)
                source.append("weflow:xor")
            except ValueError:
                pass
    if not aes_key:
        raw_aes = _dec(acct_cfg.get("imageAesKey") or cfg.get("imageAesKey"))
        if raw_aes:
            aes_key = raw_aes
            source.append("weflow:aes")

    probe, _kind, probe_path = _read_probe(account_dir)
    if aes_key and probe and not _valid_aes(probe, aes_key):
        aes_key = None
        source.append("aes-invalid")

    if not aes_key and cfg_dword:
        try:
            dword = int(cfg_dword)
        except (TypeError, ValueError):
            dword = None
        if dword:
            wxids = [os.path.basename(account_dir), own_username,
                     cfg.get("myWxid"), cfg.get("myAccountId")]
            for wxid in dict.fromkeys(x for x in wxids if x):
                cand = hashlib.md5((str(dword) + wxid).encode("utf-8")).hexdigest()[:16]
                if not probe or _valid_aes(probe, cand):
                    aes_key = cand
                    if xor_key is None:
                        xor_key = dword & 0xFF
                    source.append("cfg_dword")
                    break

    if not aes_key:
        # WeChat 4.x also exposes cfgDword in the weixin.dll cfg object; the
        # image AES key is md5(str(cfgDword)+wxid)[:16].  Read-only.
        try:
            found = keyscan_v4.find_image_key(account_dir, allow_scan=False)
            if found:
                aes_key, xor_key = found
                source.append("memory:cfgDword")
        except Exception:
            pass

    if xor_key is None:
        xor_key = _xor_from_thumbnails(account_dir)
        if xor_key is not None:
            source.append("thumbnail-tail")
    if xor_key is None:
        xor_key = 0x88
        source.append("default-0x88")

    try:
        xor_key = int(xor_key) & 0xFF
    except (TypeError, ValueError):
        xor_key = 0x88
    result = {"xor": xor_key, "aes": aes_key, "source": source,
              "probe": probe, "probe_path": probe_path}
    _IMAGE_KEY_CACHE[account_dir] = result
    return result


def _xor_from_thumbnails(account_dir):
    tails = collections.Counter()
    hits = glob.glob(os.path.join(account_dir, "msg", "attach", "*", "*", "Img", "*_t.dat"))
    for p in hits[:64]:
        try:
            with open(p, "rb") as f:
                f.seek(-2, 2)
                tail = f.read(2)
        except OSError:
            continue
        if len(tail) == 2:
            tails[(tail[0], tail[1])] += 1
    for (x, y), _n in tails.most_common(8):
        key = x ^ 0xFF
        if y ^ 0xD9 == key:
            return key
    return None


def _strip_footer(plain):
    marks = ((b"\xff\xd8", b"\xff\xd9"),
             (b"\x89PNG\r\n\x1a\n", b"\x49\x45\x4e\x44\xae\x42\x60\x82"))
    for soi, end in marks:
        if not plain.startswith(soi) or len(plain) <= len(end):
            continue
        if plain.endswith(end):
            return plain
        i = plain.rfind(end)
        if i > 0 and len(plain) - i - len(end) <= _FOOTER_MAX:
            return plain[:i + len(end)]
    return plain


def _decode_wxgf(data):
    """Many WeChat WXGF/`_W.dat` files are just single-byte XORed PNG/JPEG."""
    if data[:4] != WXGF_MAGIC and data[:4] != b"wxgf":
        return data
    head = data[:32]
    for cand in range(256):
        if _image_ext(bytes(b ^ cand for b in head)) in (".jpg", ".png", ".gif", ".webp", ".bmp"):
            return bytes(b ^ cand for b in data)
    return data


def decrypt_dat(path, aes_key, xor_key):
    """Return (plaintext bytes, container kind).  Raises ImageKeyMissing."""
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        raise ValueError("empty .dat")
    magic = data[:6]
    if magic == V2_MAGIC:
        if not aes_key:
            raise ImageKeyMissing("V2 图片需要 image_aes 密钥")
        aes_size, xor_size = struct.unpack_from("<LL", data, 6)
        aes_blk = aes_size + (16 - aes_size % 16) if aes_size % 16 else aes_size + 16
        off = 15
        aes_data = data[off:off + aes_blk]
        off += aes_blk
        raw = data[off:len(data) - xor_size]
        enc = data[len(data) - xor_size:]
        dec = Cipher(algorithms.AES(aes_key.encode("ascii")), modes.ECB()).decryptor()
        pt = dec.update(aes_data) + dec.finalize()
        pad = pt[-1] if pt else 0
        if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
            pt = pt[:-pad]
        out = _strip_footer(pt + raw + bytes(b ^ xor_key for b in enc))
        return out, "v2"
    if magic == V1_MAGIC:
        return bytes(b ^ xor_key for b in data[22:]), "v1"
    if data[:4] == WXGF_MAGIC or data[:4] == b"wxgf":
        out = _decode_wxgf(data)
        if _image_ext(out) in (".jpg", ".png", ".gif", ".webp", ".bmp"):
            return out, "wxgf-xor"
        return data, "wxgf"
    for cand in (0x88, 0x30, 0xFF, 0xE9, xor_key):
        out = bytes(b ^ (cand & 0xFF) for b in data)
        if out[:3] == b"\xff\xd8\xff" or out[:4] in (b"\x89PNG", b"GIF8", b"RIFF"):
            return out, "xor"
    # Last resort: try every single-byte XOR on the header, then apply once.
    head = data[:32]
    for cand in range(256):
        if _image_ext(bytes(b ^ cand for b in head)) in (".jpg", ".png", ".gif", ".webp", ".bmp"):
            return bytes(b ^ cand for b in data), "xor-brute"
    return data, "unknown"


def _image_ext(data):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == WXGF_MAGIC or data[:4] == b"wxgf":
        return ".wxgf"
    return ".bin"


class _ArticleText(HTMLParser):
    """Extract readable text from a WeChat article page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_content = False
        self.depth = 0
        self.skip = 0
        self.parts = []
        self.all_parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id") == "js_content":
            self.in_content = True
            self.depth = 0
        if self.in_content and tag == "div":
            self.depth += 1
        if tag in ("script", "style", "noscript"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
        if self.in_content and tag == "div":
            self.depth -= 1
            if self.depth < 0:
                self.in_content = False

    def handle_data(self, data):
        if self.skip:
            return
        text = data.strip()
        if not text:
            return
        self.all_parts.append(text)
        if self.in_content:
            self.parts.append(text)


ARTICLE_HOST = "mp.weixin.qq.com"
STICKER_HOST_SUFFIXES = (
    # WeChat/Tencent media hosts used by sticker/emoticon XML.
    ".tc.qq.com", ".qpic.cn", ".weixin.qq.com", ".wechat.com", ".wx.qq.com",
)


def _host_ok(url, exact=None, suffixes=()):
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if p.username or p.password:
        return False
    host = p.hostname.lower()
    if exact and host != exact:
        return False
    if suffixes and not any(host == s.lstrip(".") or host.endswith(s) for s in suffixes):
        return False
    return True


def fetch_article(url, timeout=20, max_bytes=5 * 1024 * 1024):
    """Fetch an official-account article and return (html, title, text).

    Only used when the caller explicitly enables --fetch-articles.  Nothing is
    uploaded; the response is kept locally.
    """
    if not _host_ok(url, exact=ARTICLE_HOST):
        raise ValueError("只允许抓取 mp.weixin.qq.com 文章")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if not _host_ok(resp.geturl(), exact=ARTICLE_HOST):
            raise ValueError("文章跳转到了不允许的域名")
        raw = resp.read(max_bytes)
        ctype = resp.headers.get("Content-Type", "")
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        enc = m.group(1)
    for cand in (enc, "utf-8", "gb18030"):
        try:
            page = raw.decode(cand, "replace")
            break
        except Exception:
            page = raw.decode("utf-8", "replace")
    title = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)', page, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:title', page, re.I)
    if m:
        title = html.unescape(m.group(1)).strip()
    if not title:
        m = re.search(r"<h1[^>]*id=[\"']activity-name[\"'][^>]*>(.*?)</h1>", page, re.S | re.I)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        if m:
            title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    parser = _ArticleText()
    try:
        parser.feed(page)
    except Exception:
        pass
    text = "\n".join(parser.parts or parser.all_parts).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return page, title, text


class MediaResolver:
    def __init__(self, data, account_dir, out_dir, args, session_username, own_username):
        self.data = data
        self.account_dir = account_dir
        self.out_dir = out_dir
        self.args = args
        self.session_username = session_username
        self.mode = getattr(args, "media_mode", "copy")
        self.voice_format = getattr(args, "voice_format", "both")
        self.fetch_articles = getattr(args, "fetch_articles", False)
        self.download_stickers = getattr(args, "download_stickers", False)
        self.max_bytes = int(getattr(args, "max_media_mb", 0) or 0) * 1024 * 1024
        self.conn = None
        try:
            self.conn = data.db("hardlink", "hardlink.db")
        except Exception:
            self.conn = None
        self.dir_cache = {}
        self.copy_cache = {}
        self.stats = collections.Counter()
        self.notes = []
        self._cache_index = None
        self._image_index = None
        self._resource_index = None
        self._sticker_index = None
        self.image_keys = _derive_image_keys(account_dir, own_username)
        self.voice_conn = None
        self.voice_chat_id = None
        try:
            self.voice_conn = data.db("message", "media_0.db")
            row = self.voice_conn.execute(
                "select rowid from Name2Id where user_name=?", (session_username,)
            ).fetchone()
            self.voice_chat_id = row[0] if row else None
        except Exception:
            self.voice_conn = None

    # -- locating ---------------------------------------------------------
    def _cache_image_candidates(self, msg):
        """WeChat's decoded-image cache (plaintext, not a screenshot).

        Paths look like:
          cache/<YYYY-MM>/Message/<md5(username)>/ImageTemp/
              <local_id>_<create_time>_hd_temp
              <local_id>_<create_time>_mid_temp[_convert]
          cache/<YYYY-MM>/Message/<md5(username)>/Thumb/
              <local_id>_<create_time>_thumb.jpg
        """
        if self._cache_index is None:
            ck = (self.account_dir, self.session_username)
            if ck in _CACHE_INDEX_CACHE:
                self._cache_index = _CACHE_INDEX_CACHE[ck]
            else:
                sid = hashlib.md5(self.session_username.encode("utf-8")).hexdigest()
                index = collections.defaultdict(list)
                root = os.path.join(self.account_dir, "cache")
                if os.path.isdir(root):
                    for month in os.listdir(root):
                        msg_dir = os.path.join(root, month, "Message", sid)
                        for sub in ("ImageTemp", "Thumb"):
                            d = os.path.join(msg_dir, sub)
                            if not os.path.isdir(d):
                                continue
                            for fn in os.listdir(d):
                                m = re.fullmatch(r"(\d+)_(\d+)_(hd_temp|mid_temp_convert|mid_temp|thumb\.jpg)", fn)
                                if not m:
                                    continue
                                tier = {"hd_temp": "full", "mid_temp_convert": "mid",
                                        "mid_temp": "mid", "thumb.jpg": "thumb"}[m.group(3)]
                                index[(int(m.group(1)), int(m.group(2)))].append(
                                    (os.path.join(d, fn), tier))
                order = {"full": 0, "mid": 1, "thumb": 2}
                for k in index:
                    index[k].sort(key=lambda x: order[x[1]])
                self._cache_index = index
                _CACHE_INDEX_CACHE[ck] = index
        lid, ts = msg.get("local_id"), msg.get("time")
        return list(self._cache_index.get((lid, ts), [])) if lid and ts else []

    def _image_fallback(self, md5):
        """Filename -> path index for msg/attach/**/Img (built once per account)."""
        if self._image_index is None:
            if self.account_dir in _IMAGE_INDEX_CACHE:
                self._image_index = _IMAGE_INDEX_CACHE[self.account_dir]
            else:
                index = {}
                root = os.path.join(self.account_dir, "msg", "attach")
                if os.path.isdir(root):
                    for r, ds, fs in os.walk(root):
                        if os.path.basename(r) != "Img":
                            continue
                        for fn in fs:
                            stem = fn
                            for suffix in ("_h.dat", "_t.dat", ".dat"):
                                if fn.endswith(suffix):
                                    stem = fn[:-len(suffix)]
                                    break
                            old = index.get(stem)
                            # Prefer _h.dat, then .dat, then _t.dat.
                            rank = 0 if fn.endswith("_h.dat") else (1 if fn.endswith(".dat") else 2)
                            if old is None or rank < old[0]:
                                index[stem] = (rank, os.path.join(r, fn))
                self._image_index = {k: v[1] for k, v in index.items()}
                _IMAGE_INDEX_CACHE[self.account_dir] = self._image_index
        if not md5:
            return None
        return self._image_index.get(md5)

    def _resource_hash(self, msg):
        """Map (local_id, create_time) to the real image file hash.

        WeChat 4.x stores this in message_resource.db MessageResourceInfo.
        packed_info contains an ASCII 32-hex file stem, which can differ from
        the md5 attribute in the message XML.
        """
        if self._resource_index is None:
            index = {}
            try:
                con = self.data.db("message", "message_resource.db")
                row = con.execute(
                    "select rowid from ChatName2Id where user_name=?", (self.session_username,)
                ).fetchone()
                if row:
                    for r in con.execute(
                        "select message_local_id, message_create_time, message_local_type, "
                        "packed_info from MessageResourceInfo where chat_id=?", (row[0],)
                    ):
                        if (r["message_local_type"] or 0) & 0xFFFFFFFF != 3:
                            continue
                        blob = r["packed_info"] or b""
                        m = re.search(rb"([0-9a-fA-F]{32})", bytes(blob))
                        if m:
                            index[(r["message_local_id"], r["message_create_time"])] = (
                                m.group(1).decode("ascii").lower())
            except Exception:
                index = {}
            self._resource_index = index
        lid, ts = msg.get("local_id"), msg.get("time")
        return self._resource_index.get((lid, ts)) if lid and ts else None

    def _dirname(self, ident):
        if ident is None:
            return None
        if ident in self.dir_cache:
            return self.dir_cache[ident]
        name = None
        if self.conn:
            try:
                row = self.conn.execute("select username from dir2id where rowid=?", (ident,)).fetchone()
                name = row[0] if row else None
            except Exception:
                name = None
        self.dir_cache[ident] = name
        return name

    def _row(self, kind, md5s):
        if not self.conn:
            return None
        table = {"image": "image_hardlink_info_v4",
                 "file": "file_hardlink_info_v4",
                 "video": "video_hardlink_info_v4"}.get(kind)
        if not table:
            return None
        mk = (self.account_dir, kind)
        index = _HARDLINK_MEM_CACHE.get(mk)
        if index is None:
            index = collections.defaultdict(list)
            try:
                cur = self.conn.execute(
                    f'select md5, file_name, dir1, dir2, file_size from "{table}"')
                for md5, fn, d1, d2, size in cur:
                    if md5:
                        index[md5].append((fn, d1, d2, size))
            except Exception:
                index = {}
            _HARDLINK_MEM_CACHE[mk] = index
        rows = []
        for md5 in md5s:
            if md5 and re.fullmatch(r"[0-9a-fA-F]{32}", md5):
                rows = index.get(md5) or []
            if rows:
                break
        return rows

    def _candidates(self, kind, row):
        fn, d1, d2 = row[0], row[1], row[2]
        d1n = self._dirname(d1)
        d2n = self._dirname(d2)
        if not fn:
            return []
        roots = []
        if kind == "image":
            if d1n and d2n:
                roots.append(os.path.join(self.account_dir, "msg", "attach", d1n, d2n, "Img"))
        elif kind == "file":
            if d1n:
                roots.append(os.path.join(self.account_dir, "msg", "file", d1n))
            if d1n and d2n:
                roots.append(os.path.join(self.account_dir, "msg", "attach", d1n, d2n, "File"))
                roots.append(os.path.join(self.account_dir, "msg", "attach", d1n, d2n))
        elif kind == "video":
            if d1n:
                roots.append(os.path.join(self.account_dir, "msg", "video", d1n))
                roots.append(os.path.join(self.account_dir, "msg", "file", d1n))
            if d1n and d2n:
                roots.append(os.path.join(self.account_dir, "msg", "attach", d1n, d2n, "Video"))
        return [os.path.join(r, fn) for r in roots]

    def _glob_first(self, patterns):
        for pat in patterns:
            try:
                hits = glob.glob(os.path.join(self.account_dir, pat))
            except (OSError, re.error):
                hits = []
            hits = [h for h in hits if os.path.isfile(h)]
            if hits:
                hits.sort(key=lambda p: -os.path.getsize(p))
                return hits[0]
        return None

    def locate(self, kind, extra, title="", msg=None):
        md5s = [extra.get("md5"), extra.get("new_md5"), extra.get("raw_md5")]
        md5s = [m for m in md5s if m]
        rows = self._row(kind, md5s) or []
        # Prefer a hardlink row whose original name matches the message title.
        if title and kind == "file":
            rows.sort(key=lambda r: (os.path.basename(r[0] or "") != os.path.basename(title),))
        for row in rows:
            for p in self._candidates(kind, row):
                if os.path.isfile(p):
                    return p
        # Fallbacks: md5-named cache files, then the original file name.
        pats = []
        md5 = next((m for m in md5s if re.fullmatch(r"[0-9a-fA-F]{32}", m or "")), "")
        if kind == "image":
            # The filename index is complete; no per-file globbing needed.
            hit = self._image_fallback(md5) if md5 else None
            if hit:
                return hit
            if msg:
                real = self._resource_hash(msg)
                if real:
                    hit = self._image_fallback(real)
                    if hit:
                        return hit
                    month = msg["time_str"][:7]
                    sid = hashlib.md5(self.session_username.encode("utf-8")).hexdigest()
                    for suffix in (".dat", "_h.dat", "_t.dat"):
                        p = os.path.join(self.account_dir, "msg", "attach", sid,
                                         month, "Img", real + suffix)
                        if os.path.isfile(p):
                            return p
                    hits = glob.glob(os.path.join(
                        self.account_dir, "msg", "attach", "*", "*", "Img",
                        glob.escape(real) + "*.dat"))
                    if hits:
                        hits.sort(key=lambda p: -os.path.getsize(p))
                        return hits[0]
            return None
        if md5:
            if kind == "image":
                pats += [f"msg/attach/*/*/Img/{md5}.dat",
                         f"msg/attach/*/*/Img/{md5}_h.dat",
                         f"msg/attach/*/*/Img/{md5}_t.dat",
                         f"msg/attach/*/*/**/{md5}*.dat"]
            elif kind == "video":
                pats += [f"msg/video/*/{md5}*.mp4", f"msg/file/*/{md5}*.mp4",
                         f"msg/attach/*/*/**/{md5}*.mp4"]
            elif kind == "file":
                pats += [f"msg/file/*/{md5}*", f"msg/attach/*/*/**/{md5}*"]
        if title:
            safe = glob.escape(os.path.basename(title))
            if kind == "file":
                pats += [f"msg/file/*/{safe}", f"msg/attach/*/*/File/{safe}"]
            elif kind == "video":
                pats += [f"msg/video/*/{safe}", f"msg/file/*/{safe}"]
        return self._glob_first(pats) if pats else None

    # -- copying ----------------------------------------------------------
    def export_record(self, record, msg, seq):
        """Resolve local media referenced by a merged-forward record item."""
        result = {"status": "n/a", "rel": None, "source": None, "size": None,
                  "note": "", "items": 0, "exported": 0}
        if not record or self.mode == "none":
            return result
        date = msg["time_str"][:10]
        for idx, item in enumerate(record.get("items") or [], 1):
            dt = item.get("datatype") or ""
            if dt not in ("2", "4", "8"):
                continue
            result["items"] += 1
            ident = item.get("dataid") or ""
            title = item.get("title") or item.get("desc") or ""
            p = None
            if dt == "8" and title:
                # Prefer an exact filename match; a same-stem .rar/.7z is not
                # the file the message refers to.
                p = self._sticker_local(os.path.basename(title))
            if not p and ident:
                p = self._sticker_local(ident)
            if not p and dt != "8" and title:
                p = self._sticker_local(os.path.splitext(os.path.basename(title))[0])
            if not p:
                item["status"] = "missing"
                item["note"] = "本地无内层媒体缓存"
                continue
            item["source"] = os.path.relpath(p, self.account_dir)
            if self.mode == "index":
                item["status"] = "indexed"
                item["note"] = "仅索引"
                continue
            name = _safe_name(f"{date}_{seq:05d}_{idx}_{os.path.basename(title) or ident}", 80)
            if dt == "2" and p.lower().endswith(".dat"):
                try:
                    plain, _kind = decrypt_dat(p, self.image_keys["aes"], self.image_keys["xor"])
                except ImageKeyMissing as exc:
                    item["status"] = "locked"
                    item["note"] = str(exc)
                    continue
                except Exception as exc:  # noqa: BLE001
                    item["status"] = "error"
                    item["note"] = str(exc)
                    continue
                ext = _image_ext(plain)
                name = os.path.splitext(name)[0] + ext
                rel, status, size = self._copy_bytes(plain, "forward", name, date)
            else:
                ext = os.path.splitext(p)[1] or (".mp4" if dt == "4" else "")
                name = os.path.splitext(name)[0] + ext
                rel, status, size = self._copy(p, "forward", name)
            item.update({"rel": rel, "status": status, "size": size})
            if status == "exported":
                result["exported"] += 1
        if result["items"]:
            result["status"] = "exported" if result["exported"] else "missing"
            result["note"] = f"内层媒体 {result['exported']}/{result['items']} 已导出"
        return result

    def export_article(self, extra, msg):
        """Optionally fetch an mp.weixin.qq.com article body (explicit opt-in)."""
        result = {"status": "n/a", "rel": None, "source": None, "size": None, "note": ""}
        url = extra.get("url") or ""
        if not url or "mp.weixin.qq.com" not in url:
            return result
        if not self.fetch_articles:
            result["note"] = "未抓取正文（加 --fetch-articles 启用）"
            return result
        if self.mode == "none":
            return result
        try:
            page, title, text = fetch_article(url)
        except Exception as exc:  # noqa: BLE001
            result.update({"status": "error", "note": f"抓取失败: {type(exc).__name__}: {exc}"})
            return result
        if not text:
            result.update({"status": "error", "note": "抓取成功但没有提取到正文"})
            return result
        base = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        date = msg["time_str"][:10]
        html_rel, _st, _sz = self._copy_bytes(page.encode("utf-8"), "articles", base + ".html", date)
        txt_rel, status, size = self._copy_bytes(text.encode("utf-8"), "articles", base + ".txt", date)
        result.update({
            "rel": txt_rel, "html_rel": html_rel, "status": status, "size": size,
            "title": title, "excerpt": text[:500], "url": url,
            "note": "公众号正文已抓取" if status == "exported" else "正文保存失败",
        })
        return result

    def _copy(self, src, subdir, name, max_bytes=None):
        size = os.path.getsize(src)
        limit = max_bytes if max_bytes is not None else self.max_bytes
        if limit and size > limit:
            return None, "too_large", size
        cache_key = (subdir, os.path.abspath(src))
        if cache_key in self.copy_cache:
            rel, cached_size = self.copy_cache[cache_key]
            return rel, "exported", cached_size
        dest_dir = os.path.join(self.out_dir, "media", subdir)
        os.makedirs(dest_dir, exist_ok=True)
        dest = _unique_path(os.path.join(dest_dir, name))
        try:
            shutil.copyfile(src, dest)
        except OSError as exc:
            return None, "error", size
        rel = os.path.relpath(dest, self.out_dir).replace(os.sep, "/")
        self.copy_cache[cache_key] = (rel, size)
        return rel, "exported", size

    def _fetch_url(self, url, timeout=20, max_bytes=5 * 1024 * 1024):
        if not _host_ok(url, suffixes=STICKER_HOST_SUFFIXES):
            raise ValueError("不允许的表情下载域名")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not _host_ok(resp.geturl(), suffixes=STICKER_HOST_SUFFIXES):
                raise ValueError("表情下载跳转到了不允许的域名")
            return resp.read(max_bytes)

    def _sticker_local(self, ident):
        if self._sticker_index is None:
            if self.account_dir in _STICKER_INDEX_CACHE:
                self._sticker_index = _STICKER_INDEX_CACHE[self.account_dir]
            else:
                index = {}
                for sub in ("msg", "cache", "emoticon"):
                    root = os.path.join(self.account_dir, sub)
                    if not os.path.isdir(root):
                        continue
                    for r, _d, fs in os.walk(root):
                        for fn in fs:
                            p = os.path.join(r, fn)
                            stem = os.path.splitext(fn)[0].lower()
                            index.setdefault(stem, p)
                            index.setdefault(fn.lower(), p)
                self._sticker_index = index
                _STICKER_INDEX_CACHE[self.account_dir] = index
        return self._sticker_index.get(ident.lower()) if ident else None

    def _sticker_bytes(self, extra):
        """Return (bytes, source_note) for a sticker: local cache or CDN."""
        md5 = (extra.get("md5") or "").lower()
        extern = (extra.get("extern_md5") or "").lower()
        for ident in (md5, extern):
            if not ident:
                continue
            p = self._sticker_local(ident)
            if p and os.path.isfile(p):
                with open(p, "rb") as f:
                    data = f.read()
                if _image_ext(data) != ".bin":
                    return data, "本地缓存"
        if not self.download_stickers:
            return None, "本地无缓存（加 --download-stickers 可尝试下载）"
        urls = [extra.get("cdnurl"), extra.get("externurl"), extra.get("encrypturl")]
        aeskey = extra.get("aeskey") or ""
        last = ""
        for url in urls:
            if not url:
                continue
            try:
                data = self._fetch_url(url)
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                continue
            if _image_ext(data) != ".bin":
                return data, "从微信 CDN 下载"
            # Some CDN payloads are AES-128-ECB encrypted; try the message aeskey.
            keys = []
            if re.fullmatch(r"[0-9a-fA-F]{32}", aeskey):
                keys.append(bytes.fromhex(aeskey))
            if aeskey:
                keys.append(aeskey.encode("utf-8")[:16])
            for key in keys:
                try:
                    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(data)
                except Exception:
                    continue
                if _image_ext(dec) != ".bin":
                    return dec, "从微信 CDN 下载并解密"
            last = "下载内容不是可识别图片"
        return None, last or "没有可用的表情 URL"

    def _export_sticker(self, extra, msg, seq):
        result = {"status": "missing", "rel": None, "source": None, "size": None, "note": ""}
        if self.mode == "none":
            return result
        data, note = self._sticker_bytes(extra)
        if not data:
            result["note"] = note
            return result
        ext = _image_ext(data)
        date = msg["time_str"][:10]
        name = f"{date}_{seq:05d}_{_short_md5(extra.get('md5'))}{ext}"
        rel, status, size = self._copy_bytes(data, "stickers", name, date)
        result.update({"rel": rel, "status": status, "size": size, "note": note})
        return result

    def export(self, kind, extra, msg, seq, title=""):
        result = {"status": "none", "rel": None, "source": None,
                  "size": None, "note": ""}
        if self.mode == "none":
            return result
        cache = self._cache_image_candidates(msg) if kind in ("image", "sticker") else []
        src = self.locate(kind, extra, title or "", msg)
        if src:
            result["source"] = os.path.relpath(src, self.account_dir)
            result["size"] = os.path.getsize(src)
        date = msg["time_str"][:10]
        seq_name = f"{date}_{seq:05d}_{_short_md5(extra.get('md5'))}"

        if kind == "sticker":
            return self._export_sticker(extra, msg, seq)

        if kind == "image":
            if self.mode == "index":
                chosen = (cache[0][0] if cache else src)
                if chosen:
                    result["source"] = os.path.relpath(chosen, self.account_dir)
                    result["status"] = "indexed"
                    result["note"] = "仅索引，未复制（--media-mode copy 可导出）"
                else:
                    result["status"] = "missing"
                    result["note"] = "本地缓存不存在（微信里未下载或已清理）"
                return result
            # 1) WeChat already decoded this image into a plaintext cache entry.
            for p, tier in cache:
                try:
                    with open(p, "rb") as f:
                        raw = f.read()
                except OSError:
                    continue
                raw = _decode_wxgf(raw)
                ext = _image_ext(raw)
                if ext in (".jpg", ".png", ".gif", ".webp", ".bmp"):
                    rel, status, size = self._copy_bytes(raw, "images", seq_name + ext, date)
                    result.update({
                        "rel": rel, "status": status, "size": size,
                        "source": os.path.relpath(p, self.account_dir),
                        "note": f"来自微信解码缓存（{tier}，无需图片密钥）",
                    })
                    return result
            # 2) Encrypted .dat, if the image AES key is available.
            if src:
                candidates = [src]
                if src.endswith(".dat") and not src.endswith(("_h.dat", "_t.dat")):
                    for suffix in ("_h.dat", "_t.dat"):
                        alt = src[:-4] + suffix
                        if os.path.isfile(alt):
                            candidates.insert(0, alt)
                last_err = ""
                for p in candidates:
                    try:
                        plain, _ckind = decrypt_dat(
                            p, self.image_keys["aes"], self.image_keys["xor"])
                    except ImageKeyMissing as exc:
                        result["status"] = "locked"
                        result["note"] = str(exc) + "（把 image_aes 写进 keys.json）"
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_err = str(exc)
                        continue
                    ext = _image_ext(plain)
                    rel, status, size = self._copy_bytes(plain, "images", seq_name + ext, date)
                    note = ""
                    if status == "exported" and ext in (".wxgf", ".bin"):
                        status = "unsupported"
                        note = "WXGF/未知容器，浏览器无法直接显示；原始字节已保存"
                    result.update({"rel": rel, "status": status, "size": size,
                                   "source": os.path.relpath(p, self.account_dir),
                                   "note": note})
                    return result
                if last_err and result["status"] == "none":
                    result["status"] = "error"
                    result["note"] = last_err or "无法解密图片"
            # 3) Cache exists but is WXGF: save the bytes with an explicit status.
            for p, tier in cache:
                try:
                    with open(p, "rb") as f:
                        raw = f.read()
                except OSError:
                    continue
                raw = _decode_wxgf(raw)
                ext = _image_ext(raw)
                if ext in (".jpg", ".png", ".gif", ".webp", ".bmp"):
                    rel, status, size = self._copy_bytes(raw, "images", seq_name + ext, date)
                    result.update({
                        "rel": rel, "status": status, "size": size,
                        "source": os.path.relpath(p, self.account_dir),
                        "note": f"WXGF 已转普通图片（{tier}）",
                    })
                    return result
                rel, status, size = self._copy_bytes(raw, "images", seq_name + ext, date)
                result.update({
                    "rel": rel, "status": "unsupported", "size": size,
                    "source": os.path.relpath(p, self.account_dir),
                    "note": f"缓存为 WXGF/未知容器（{tier}），浏览器无法直接显示",
                })
                return result
            if result["status"] == "none":
                if src:
                    result["status"] = "locked"
                    result["note"] = "图片已找到但缺少 image_aes，无法解密"
                else:
                    result["status"] = "missing"
                    result["note"] = "本地缓存不存在（微信里未下载或已清理）"
            return result

        if not src:
            result["status"] = "missing"
            result["note"] = "本地缓存不存在（微信里未下载或已清理）"
            return result
        if self.mode == "index":
            result["status"] = "indexed"
            result["note"] = "仅索引，未复制（--media-mode copy 可导出）"
            return result
        if kind == "video":
            rel, status, size = self._copy(src, "videos", seq_name + os.path.splitext(src)[1].lower())
        elif kind == "file":
            orig = os.path.basename(title or extra.get("title") or os.path.basename(src))
            rel, status, size = self._copy(src, "files", f"{seq_name}_{_safe_name(orig, 80)}")
        elif kind == "voice":
            rel, status, size = self._copy(src, "voices", seq_name + (os.path.splitext(src)[1] or ".silk"))
        else:
            rel, status, size = self._copy(src, "other", seq_name + os.path.splitext(src)[1])
        result.update({"rel": rel, "status": status, "size": size})
        if status == "too_large":
            result["note"] = f"超过 --max-media-mb 限制（{_fmt_size(size)}）"
        return result

    def _copy_bytes(self, data, subdir, name, date):
        if self.max_bytes and len(data) > self.max_bytes:
            return None, "too_large", len(data)
        dest_dir = os.path.join(self.out_dir, "media", subdir, date)
        os.makedirs(dest_dir, exist_ok=True)
        dest = _unique_path(os.path.join(dest_dir, name))
        try:
            with open(dest, "wb") as f:
                f.write(data)
        except OSError:
            return None, "error", len(data)
        rel = os.path.relpath(dest, self.out_dir).replace(os.sep, "/")
        return rel, "exported", len(data)

    def _finish_voice(self, result, rel_silk, size, date):
        """Keep SILK and/or decode to a playable WAV under media/voices."""
        result.update({"rel": rel_silk, "status": "exported", "size": size})
        want_wav = self.voice_format in ("wav", "both")
        if not want_wav:
            result["note"] = "SILK 原始语音"
            return result
        abs_silk = os.path.join(self.out_dir, rel_silk.replace("/", os.sep))
        abs_wav = os.path.splitext(abs_silk)[0] + ".wav"
        ok, detail = silkdec.to_wav(abs_silk, abs_wav)
        if ok:
            rel_wav = os.path.relpath(abs_wav, self.out_dir).replace(os.sep, "/")
            if self.voice_format == "wav":
                silkdec.cleanup(abs_silk)
            result.update({
                "rel": rel_wav, "wav_rel": rel_wav,
                "silk_rel": rel_silk if self.voice_format == "both" else None,
                "size": os.path.getsize(abs_wav),
                "note": "SILK 已转 WAV（原始 SILK 同时保留）"
                        if self.voice_format == "both" else "SILK 已转 WAV",
            })
            return result
        # No decoder: still keep the raw SILK and report why.
        result["note"] = f"已导出 SILK；未转 WAV（{detail}）"
        return result

    def export_voice(self, extra, msg, seq):
        """Voice bytes live in media_0.db, not on disk; best effort."""
        result = {"status": "missing", "rel": None, "source": None, "size": None, "note": ""}
        if self.mode == "none":
            return result
        if self.voice_conn is not None and self.voice_chat_id is not None:
            try:
                row = self.voice_conn.execute(
                    "select voice_data, svr_id from VoiceInfo "
                    "where chat_name_id=? and (local_id=? or svr_id=?) "
                    "order by create_time desc limit 1",
                    (self.voice_chat_id, msg["local_id"], msg["server_id"] or 0)
                ).fetchone()
            except Exception:
                row = None
            if row and row[0]:
                blob = bytes(row[0])
                date = msg["time_str"][:10]
                name = f"{date}_{seq:05d}_{_short_md5(extra.get('md5'))}.silk"
                rel, status, size = self._copy_bytes(blob, "voices", name, date)
                if status != "exported":
                    result.update({"rel": rel, "status": status, "size": size})
                    return result
                return self._finish_voice(result, rel, size, date)
        # Fallback: a voicemd5-named file somewhere in the account.
        md5 = extra.get("md5") or ""
        if md5:
            p = self._glob_first([f"msg/**/{glob.escape(md5)}*"])
            if p:
                result["source"] = os.path.relpath(p, self.account_dir)
                if self.mode == "index":
                    result.update({"status": "indexed",
                                   "note": "仅索引，未复制"})
                    return result
                rel, status, size = self._copy(
                    p, "voices", f"{msg['time_str'][:10]}_{seq:05d}_{_short_md5(md5)}{os.path.splitext(p)[1]}")
                if status != "exported":
                    result.update({"rel": rel, "status": status, "size": size})
                    return result
                return self._finish_voice(result, rel, size, msg["time_str"][:10])
        result["note"] = "语音未落盘（微信 4.x 仅保留在 media_0.db 或已清理）"
        return result


# ---------------------------------------------------------------- renderers

_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#ededed;font:15px/1.55 "Microsoft YaHei",system-ui,sans-serif;color:#222}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #ddd;padding:10px 18px}
header h1{font-size:17px;margin:0 0 3px}
header .sub{color:#888;font-size:12px}
.toolbar{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.toolbar input{flex:1;min-width:180px;padding:6px 9px;border:1px solid #ccc;border-radius:6px}
.toolbar select{padding:6px 8px;border:1px solid #ccc;border-radius:6px;background:#fff}
main{max-width:900px;margin:0 auto;padding:16px 18px 60px}
.day{text-align:center;color:#999;font-size:12px;margin:20px 0 10px}
.m{display:flex;margin:8px 0}
.m.self{justify-content:flex-end}
.avatar{width:34px;height:34px;border-radius:4px;background:#c9d6e8;color:#456;display:flex;align-items:center;justify-content:center;font-size:13px;flex:0 0 34px;margin:0 8px;user-select:none}
.bwrap{max-width:76%}
.meta{font-size:11px;color:#999;margin:0 2px 3px}
.m.self .meta{text-align:right}
.bubble{background:#fff;border-radius:6px;padding:8px 11px;white-space:pre-wrap;word-break:break-word;box-shadow:0 1px 1px rgba(0,0,0,.06)}
.m.self .bubble{background:#95ec69}
.sys{text-align:center;color:#999;font-size:12px;margin:12px 0;white-space:pre-wrap}
.kind{color:#7a7a7a;font-style:italic}
.media img,.media video{max-width:100%;max-height:420px;border-radius:6px;display:block;margin-top:4px}
.media audio{width:100%;margin-top:4px}
.file{display:inline-block;margin-top:2px;padding:6px 9px;background:#f6f7f9;border:1px solid #e0e3e8;border-radius:6px;text-decoration:none;color:#2a6cb8}
.file .size{color:#999;font-size:12px;margin-left:6px}
.warn{color:#b06a00;font-size:12px}
.fwd{margin-top:4px;border:1px solid #e0e3e8;border-radius:6px;padding:6px 8px;background:#fafbfc}
.fwd summary{cursor:pointer;font-weight:600}
.fwd-desc{color:#888;font-size:12px;margin:4px 0}
.fwd-item{border-top:1px solid #eef0f2;padding:5px 0;font-size:13px}
.fwd-meta{color:#999;font-size:11px;margin-right:6px}
.quote{border-left:3px solid #d0d5dd;background:#f7f8fa;padding:5px 8px;margin-bottom:5px;color:#666;font-size:13px}
.quote .qmeta{color:#999;font-size:11px;margin-bottom:2px}
a{color:#2a6cb8}
@media print{.toolbar{display:none}body{background:#fff}}
"""


def _media_block(m):
    md = m.get("media") or {}
    kind = m["kind"]
    rel = md.get("rel")
    status = md.get("status") or "none"
    title = (m.get("extra") or {}).get("title") or ""
    if kind == "image":
        if rel and status == "exported":
            e = html.escape(rel, quote=True)
            return f'<div class="media"><a href="{e}"><img src="{e}" loading="lazy" alt="图片"></a></div>'
        return f'<div class="warn">[图片 · {html.escape(md.get("note") or status)}]</div>'
    if kind == "video":
        if rel and status == "exported":
            e = html.escape(rel, quote=True)
            return f'<div class="media"><video controls preload="metadata" src="{e}"></video></div>'
        return f'<div class="warn">[视频 · {html.escape(md.get("note") or status)}]</div>'
    if kind == "file":
        label = html.escape(title or "文件")
        if rel and status == "exported":
            e = html.escape(rel, quote=True)
            return (f'<a class="file" href="{e}">📎 {label}'
                    f'<span class="size">{_fmt_size(md.get("size"))}</span></a>')
        return f'<div class="warn">📎 {label}（{html.escape(md.get("note") or "本地文件未找到")}）</div>'
    if kind == "voice":
        if rel and status == "exported":
            e = html.escape(rel, quote=True)
            if rel.lower().endswith((".wav", ".mp3", ".ogg", ".m4a")):
                return f'<div class="media"><audio controls src="{e}"></audio></div>'
            return f'<a class="file" href="{e}">🔊 语音（SILK，需转换）</a>'
        return f'<div class="warn">[语音 · {html.escape(md.get("note") or status)}]</div>'
    if kind == "sticker":
        if rel and status == "exported":
            e = html.escape(rel, quote=True)
            return f'<div class="media"><img src="{e}" loading="lazy" alt="表情"></div>'
        return '<span class="kind">[表情]</span>'
    return None


def _render_record(record):
    if not record:
        return ""
    title = html.escape(record.get("title") or "合并转发")
    desc = html.escape(record.get("desc") or "")
    rows = []
    labels = {"1": "文字", "2": "图片", "3": "语音", "4": "视频",
              "8": "文件", "17": "位置", "19": "合并转发"}
    for item in record.get("items") or []:
        who = html.escape(item.get("sender") or "")
        when = html.escape(item.get("time") or "")
        body = item.get("desc") or ""
        if not body and item.get("title"):
            body = f"[{labels.get(item.get('datatype'), '消息')}] {item.get('title')}"
        if item.get("fmt") and item.get("size"):
            body += f" ({html.escape(item['fmt'])}, {_fmt_size(item['size'])})"
        link = ""
        if item.get("rel"):
            r = html.escape(item["rel"], quote=True)
            if item.get("datatype") == "2":
                link = f'<br><a href="{r}"><img src="{r}" loading="lazy" alt="内层图片" style="max-width:220px"></a>'
            else:
                link = f'<br><a href="{r}">下载内层媒体</a>'
        rows.append(
            f'<div class="fwd-item"><span class="fwd-meta">{who} {when}</span>'
            f'<div>{html.escape(body)}{link}</div></div>')
    desc_html = f'<div class="fwd-desc">{desc}</div>' if desc else ""
    return (
        f'<details class="fwd"><summary>{title}（{len(rows)} 条）</summary>'
        f'{desc_html}{"".join(rows)}</details>')


def _render_quote(quote):
    if not quote:
        return ""
    sender = html.escape(quote.get("sender") or "")
    when = html.escape(quote.get("time") or "")
    text = html.escape(quote.get("text") or "")
    qx = quote.get("extra") or {}
    if qx.get("url"):
        u = html.escape(qx["url"], quote=True)
        text += f'<br><a href="{u}">{u}</a>'
    return (
        f'<div class="quote"><div class="qmeta">引用 {sender} {when}</div>'
        f'<div>{text}</div></div>')


def render_body(m):
    kind = m["kind"]
    extra = m.get("extra") or {}
    if kind == "app":
        if extra.get("quote"):
            reply = html.escape(extra.get("title") or "")
            return _render_quote(extra["quote"]) + f'<div>{reply}</div>'
        if extra.get("record"):
            return _render_record(extra["record"])
        parts = []
        if extra.get("title"):
            parts.append(f'<b>{html.escape(extra["title"])}</b>')
        if extra.get("des"):
            parts.append(html.escape(extra["des"]))
        url = extra.get("url") or ""
        if url:
            u = html.escape(url, quote=True)
            parts.append(f'<a href="{u}">{u}</a>')
        art = m.get("media") or {}
        if art.get("rel"):
            r = html.escape(art["rel"], quote=True)
            parts.append(f'<a href="{r}">📄 本地正文</a>')
            if art.get("excerpt"):
                parts.append(f'<div class="fwd-desc">{html.escape(art["excerpt"][:300])}…</div>')
        return "<br>".join(parts) or html.escape(m["text"])
    if kind in ("image", "video", "file", "voice", "sticker"):
        block = _media_block(m)
        if block:
            return block
        return html.escape(m["text"])
    return html.escape(m["text"])


def render_html(meta, messages, media_summary):
    out = []
    last_day = None
    for m in messages:
        day = m["time_str"][:10]
        if day != last_day:
            out.append(f'<div class="day">{day}</div>')
            last_day = day
        if m["kind"] == "system":
            out.append(f'<div class="sys" data-kind="system">{html.escape(m["text"])}</div>')
            continue
        cls = "m self" if m["is_self"] else "m"
        who = "我" if m["is_self"] else (m["sender_name"] or m["sender"] or "未知")
        initial = html.escape(who[:1])
        text_for_filter = html.escape((m["text"] or "") + " " + (m.get("media", {}).get("note") or ""))
        bubble = render_body(m)
        meta_line = f"{html.escape(who)} · {m['time_str'][11:16]}"
        out.append(
            f'<div class="{cls}" data-kind="{m["kind"]}" data-text="{text_for_filter}">'
            f'<div class="avatar">{initial}</div>'
            f'<div class="bwrap"><div class="meta">{meta_line}</div>'
            f'<div class="bubble">{bubble}</div></div></div>'
        )
    title = html.escape(meta["title"])
    sub = (f'{html.escape(meta["username"])} · {meta["message_count"]} 条消息 · '
           f'{html.escape(str(meta.get("first_time") or ""))} ~ {html.escape(str(meta.get("last_time") or ""))}')
    media_line = " · ".join(f"{k}:{v}" for k, v in sorted(media_summary.items()) if v)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{_CSS}</style></head><body>
<header><h1>{title}</h1><div class="sub">{sub}</div>
<div class="sub">媒体状态：{html.escape(media_line or "无")}</div>
<div class="toolbar"><input id="q" placeholder="搜索本会话文字…">
<select id="kind"><option value="">全部类型</option>
<option value="text">文字</option><option value="image">图片</option>
<option value="video">视频</option><option value="file">文件</option>
<option value="voice">语音</option><option value="app">链接/应用</option></select></div>
</header><main>{''.join(out)}</main>
<script>
const q=document.getElementById('q'),k=document.getElementById('kind');
function f(){{const t=q.value.toLowerCase(),v=k.value;
document.querySelectorAll('main > div[data-kind]').forEach(d=>{{
 const okT=!t||(d.dataset.text||'').toLowerCase().includes(t);
 const okK=!v||d.dataset.kind===v||(v==='app'&&d.dataset.kind==='file');
 d.style.display=(okT&&okK)?'':'none';}});}}
q.oninput=f;k.onchange=f;
</script></body></html>
"""


def render_markdown(meta, messages):
    lines = [f'# {meta["title"]}', "",
             f'- 会话：`{meta["username"]}`',
             f'- 消息数：{meta["message_count"]}',
             f'- 时间：{meta.get("first_time")} ~ {meta.get("last_time")}', ""]
    last_day = None
    for m in messages:
        day = m["time_str"][:10]
        if day != last_day:
            lines += [f"## {day}", ""]
            last_day = day
        if m["kind"] == "system":
            lines += [f"> {m['text']}", ""]
            continue
        who = "我" if m["is_self"] else (m["sender_name"] or m["sender"] or "未知")
        lines.append(f"**{who}** · {m['time_str'][11:19]}")
        md = m.get("media") or {}
        rel = md.get("rel")
        if m["kind"] == "image" and rel:
            lines.append(f"![图片]({rel})")
        elif m["kind"] == "video" and rel:
            lines.append(f"[🎬 视频]({rel})")
        elif m["kind"] == "file":
            label = (m.get("extra") or {}).get("title") or "文件"
            lines.append(f"[📎 {label}]({rel})" if rel else f"📎 {label}（本地未找到）")
        elif m["kind"] == "voice":
            lines.append(f"[🔊 语音]({rel})" if rel else "[语音]")
        elif m["kind"] == "sticker":
            lines.append(f"![表情]({rel})" if rel else "[表情]")
        elif m["kind"] == "app":
            quote = (m.get("extra") or {}).get("quote")
            record = (m.get("extra") or {}).get("record")
            if quote:
                lines.append(f"> 引用 **{quote.get('sender') or ''}** {quote.get('time') or ''}: {quote.get('text') or ''}")
                lines.append(f">")
                lines.append(m.get("extra", {}).get("title") or m["text"] or "")
            elif record:
                lines.append(f"**[合并转发] {record.get('title') or ''}**")
                for item in record.get("items") or []:
                    body = item.get("desc") or item.get("title") or ""
                    lines.append(f"- **{item.get('sender') or ''}** {item.get('time') or ''}: {body}")
            else:
                lines.append(m["text"] or "")
        else:
            lines.append(m["text"])
        if md.get("note") and md.get("status") != "exported":
            lines.append(f"> {md['note']}")
        lines.append("")
    return "\n".join(lines)


def render_txt(messages):
    out = []
    for m in messages:
        who = "我" if m["is_self"] else (m["sender_name"] or m["sender"] or "?")
        text = (m["text"] or "").replace("\r", " ")
        md = m.get("media") or {}
        suffix = ""
        if md.get("rel"):
            suffix = f"  [media: {md['rel']}]"
        elif md.get("status") in ("locked", "missing"):
            suffix = f"  [{m['kind']}: {md.get('status')}]"
        out.append(f'[{m["time_str"]}] {who}: {text}{suffix}')
    return "\n".join(out) + "\n"


def render_index(root, entries):
    rows = []
    for e in entries:
        rel = html.escape(e["link"], quote=True)
        rows.append(
            f'<tr><td><a href="{rel}">{html.escape(e["title"])}</a></td>'
            f'<td>{html.escape(e["username"])}</td><td>{e["count"]}</td>'
            f'<td>{html.escape(e.get("range", ""))}</td>'
            f'<td>{html.escape(e.get("media", ""))}</td></tr>'
        )
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>微信导出目录</title><style>{_CSS}
table{{width:100%;border-collapse:collapse;background:#fff}}
th,td{{padding:8px 10px;border-bottom:1px solid #eee;text-align:left;font-size:13px}}
th{{background:#fafafa}}</style></head><body><main>
<h1>微信聊天记录导出</h1>
<p class="sub">共 {len(entries)} 个会话 · 生成于 {datetime.datetime.now():%Y-%m-%d %H:%M:%S}</p>
<table><thead><tr><th>会话</th><th>username</th><th>消息数</th><th>时间范围</th><th>媒体</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></main></body></html>
"""


# ---------------------------------------------------------------- export flow

def _session_kind(username):
    if username.endswith("@chatroom"):
        return "group"
    if username.startswith("gh_"):
        return "official"
    if username in ("filehelper", "weixin", "brandsessionholder",
                    "brandservicesessionholder", "brandsessionholder"):
        return "system"
    return "private"


def select_sessions(data, args):
    want = getattr(args, "kind", "all") or "all"
    selected = []
    needles = getattr(args, "session", None)
    if isinstance(needles, str):
        needles = [needles]
    if needles:
        for needle in needles:
            for part in str(needle).split(","):
                part = part.strip()
                if part:
                    selected.append(wfchat_find_session(data, part))
    else:
        limit = int(getattr(args, "limit_sessions", 0) or 0)
        for row in wfchat_session_rows(data):
            u = row["username"]
            if want != "all" and _session_kind(u) != want:
                continue
            if getattr(args, "since", None):
                lo = parse_when(args.since)
                if lo and (row["sort_timestamp"] or 0) < lo:
                    continue
            selected.append(u)
            if limit and len(selected) >= limit:
                break
    seen = set()
    return [u for u in selected if not (u in seen or seen.add(u))]


def wfchat_session_rows(data):
    import wfchat
    return wfchat.session_rows(data)


def wfchat_find_session(data, needle):
    import wfchat
    return wfchat.find_session(data, needle)


def _prepare_messages(messages, resolver):
    stats = collections.Counter()
    for seq, m in enumerate(messages, 1):
        kind = m["kind"]
        extra = m.get("extra") or {}
        try:
            if kind in ("image", "video", "file", "sticker"):
                title = extra.get("title") or m.get("text") or ""
                media = resolver.export(kind, extra, m, seq, title)
            elif kind == "voice":
                media = resolver.export_voice(extra, m, seq)
            elif kind == "app":
                if extra.get("record"):
                    media = resolver.export_record(extra["record"], m, seq)
                else:
                    media = resolver.export_article(extra, m)
            else:
                media = {"status": "n/a", "rel": None, "source": None, "size": None, "note": ""}
        except Exception as exc:  # noqa: BLE001
            media = {"status": "error", "rel": None, "source": None,
                     "size": None, "note": f"{type(exc).__name__}: {exc}"}
        m["media"] = media
        stats[media["status"]] += 1
        if kind in MEDIA_KINDS:
            stats["media_" + kind] += 1
    return stats


def _check_export(picked, status_counts, sess_dir, source_total, media_mode):
    """Consistency checks that can be done without re-reading WeChat."""
    known = set(EXPORT_KINDS)
    unhandled = collections.Counter(
        m.get("kind") for m in picked if m.get("kind") not in known)
    media_msgs = [m for m in picked if m.get("kind") in MEDIA_KINDS]
    missing_rel = []
    zero_size = []
    for m in media_msgs:
        md = m.get("media") or {}
        rel = md.get("rel")
        if rel:
            p = os.path.join(sess_dir, rel.replace("/", os.sep))
            if not os.path.isfile(p):
                missing_rel.append(rel)
            elif os.path.getsize(p) == 0:
                zero_size.append(rel)
    status = collections.Counter(m.get("media", {}).get("status") for m in media_msgs)
    accounted = sum(v for k, v in status.items() if k != "none")
    checks = {
        "source_message_count": source_total,
        "exported_message_count": len(picked),
        "filtered_out": max(0, source_total - len(picked)),
        "media_message_count": len(media_msgs),
        "media_status": dict(status),
        "unhandled_kinds": dict(unhandled),
        "missing_rel": missing_rel[:20],
        "zero_size_rel": zero_size[:20],
        "message_count_ok": source_total >= len(picked),
        "media_files_ok": not missing_rel and not zero_size,
        "media_accounted": (media_mode == "none") or accounted >= len(media_msgs),
        "unhandled_kinds_ok": not unhandled,
    }
    checks["ok"] = all(checks[k] for k in (
        "message_count_ok", "media_files_ok", "media_accounted", "unhandled_kinds_ok"))
    return checks


def run_export(args):
    import wfchat

    if not getattr(args, "session", None) and not getattr(args, "all", False):
        raise SystemExit("请用 --session 指定会话，或加 --all 导出全部匹配会话")

    account_dir, data = wfchat.open_account(args)
    out = getattr(args, "out", None) or os.path.join(
        ec.WORK, "export_" + _now_stamp())
    out = os.path.abspath(out)
    if os.path.isdir(out) and os.listdir(out) and not getattr(args, "overwrite", False):
        out = out + "_" + _now_stamp()
    os.makedirs(out, exist_ok=True)

    formats = [f.strip() for f in (getattr(args, "format", "html,json") or "").split(",")
               if f.strip() in ("html", "json", "md", "txt")]
    if not formats:
        formats = ["html", "json"]
    sessions = select_sessions(data, args)
    if not sessions:
        raise SystemExit("没有匹配的会话")

    own = data.own_username()
    results = []
    since = parse_when(getattr(args, "since", None))
    until = parse_when(getattr(args, "until", None), until=True)
    for idx, username in enumerate(sessions, 1):
        try:
            meta, messages = ec.collect(data, username, since=since, until=until)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[{idx}/{len(sessions)}] 跳过 {username}: {exc}\n")
            continue
        picked = filter_messages(messages, args)
        if not picked:
            continue
        title = meta["title"] or username
        folder = _safe_name(f"{title}_{username.split('@')[0]}", 60)
        sess_dir = os.path.join(out, folder)
        if getattr(args, "flat", False):
            sess_dir = out
        os.makedirs(sess_dir, exist_ok=True)

        resolver = MediaResolver(data, account_dir, sess_dir, args, username, own)
        status_counts = _prepare_messages(picked, resolver)
        checks = _check_export(picked, status_counts, sess_dir, len(messages),
                               getattr(args, "media_mode", "copy"))
        meta["message_count"] = len(picked)
        meta["first_time"] = picked[0]["time_str"] if picked else None
        meta["last_time"] = picked[-1]["time_str"] if picked else None
        meta["media"] = {k: v for k, v in status_counts.items() if not k.startswith("media_")}
        meta["media_counts"] = {k[6:]: v for k, v in status_counts.items() if k.startswith("media_")}
        meta["filters"] = {
            "since": getattr(args, "since", None),
            "until": getattr(args, "until", None),
            "types": getattr(args, "types", None),
            "keyword": getattr(args, "keyword", None),
            "sender": getattr(args, "sender", None),
            "last": getattr(args, "last", None),
        }
        image_note = ("image keys: aes=" + ("yes" if resolver.image_keys.get("aes") else "no")
                      + ", xor=" + ("yes" if resolver.image_keys.get("xor") is not None else "no")
                      + " (" + ",".join(resolver.image_keys.get("source") or []) + ")")
        manifest = {
            "session": meta,
            "account": os.path.basename(account_dir),
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "formats": formats,
            "media_mode": getattr(args, "media_mode", "copy"),
            "media_status": dict(status_counts),
            "image_key": image_note,
            "warnings": resolver.notes,
            "checks": checks,
        }
        flat = getattr(args, "flat", False)
        base = "chat" if not flat else _safe_name(
            f"chat_{title}_{username.split('@')[0]}", 60)
        if "json" in formats:
            with open(os.path.join(sess_dir, base + ".json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
                f.write("\n")
            with open(os.path.join(sess_dir, base + "_messages.json"), "w", encoding="utf-8") as f:
                json.dump(picked, f, ensure_ascii=False, indent=1)
                f.write("\n")
        if "html" in formats:
            with open(os.path.join(sess_dir, base + ".html"), "w", encoding="utf-8") as f:
                f.write(render_html(meta, picked, status_counts))
        if "md" in formats:
            with open(os.path.join(sess_dir, base + ".md"), "w", encoding="utf-8") as f:
                f.write(render_markdown(meta, picked))
        if "txt" in formats:
            with open(os.path.join(sess_dir, base + ".txt"), "w", encoding="utf-8") as f:
                f.write(render_txt(picked))
        manifest_name = "manifest.json" if not flat else base + "_manifest.json"
        with open(os.path.join(sess_dir, manifest_name), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
            f.write("\n")

        rel_link = os.path.relpath(os.path.join(sess_dir, base + ".html"), out).replace(os.sep, "/")
        results.append({
            "title": title,
            "username": username,
            "count": len(picked),
            "range": f"{meta['first_time']} ~ {meta['last_time']}",
            "media": ", ".join(f"{k}:{v}" for k, v in meta["media"].items() if v),
            "dir": sess_dir,
            "link": rel_link if "html" in formats else os.path.relpath(sess_dir, out),
            "checks": checks,
        })
        sys.stderr.write(
            f"[{idx}/{len(sessions)}] {title}: {len(picked)} 条, "
            f"{', '.join(f'{k}={v}' for k, v in meta['media'].items() if v) or '无媒体'}"
            f"{'' if checks['ok'] else '  [CHECK FAILED]'}\n"
        )
        if not checks["ok"]:
            sys.stderr.write(
                f"  检查未通过: unhandled={checks['unhandled_kinds']}, "
                f"missing_rel={len(checks['missing_rel'])}, "
                f"zero_size={len(checks['zero_size_rel'])}\n"
            )

    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index(out, results))
    summary = {"out": out, "sessions": len(results), "messages": sum(r["count"] for r in results),
               "items": results}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0
