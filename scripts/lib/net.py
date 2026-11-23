# -*- coding: utf-8 -*-
"""Small, allowlisted network helpers for opt-in enrichment.

Only used when the caller explicitly enables --fetch-articles or
--download-stickers.  GET only, no upload, size capped, redirect host checked.
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from Crypto.Cipher import AES

ARTICLE_HOST = "mp.weixin.qq.com"
STICKER_HOST_SUFFIXES = (
    ".tc.qq.com", ".qpic.cn", ".weixin.qq.com", ".wechat.com", ".wx.qq.com",
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_BYTES = 5 * 1024 * 1024


def host_ok(url: str, exact: str = "", suffixes=()) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    if exact and host != exact:
        return False
    if suffixes and not any(
            host == suffix.lstrip(".") or host.endswith(suffix) for suffix in suffixes):
        return False
    return True


def fetch_bytes(url: str, timeout: int = 20, max_bytes: int = MAX_BYTES,
                exact: str = "", suffixes=()):
    if not host_ok(url, exact=exact, suffixes=suffixes):
        raise ValueError("域名不在允许列表")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not host_ok(response.geturl(), exact=exact, suffixes=suffixes):
            raise ValueError("重定向到了不允许的域名")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("响应超过大小上限")
        return data, response.headers.get("Content-Type", "")


class _ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_content = False
        self.depth = 0
        self.skip = 0
        self.parts = []
        self.all_parts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id") == "js_content":
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


def article_title(page: str) -> str:
    match = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)', page, re.I)
    if not match:
        match = re.search(
            r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:title', page, re.I)
    if match:
        return html.unescape(match.group(1)).strip()
    match = re.search(
        r"<h1[^>]*id=[\"']activity-name[\"'][^>]*>(.*?)</h1>", page, re.S | re.I)
    if match:
        return re.sub(r"<[^>]+>", "", match.group(1)).strip()
    match = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    if match:
        return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    return ""


def fetch_article(url: str, timeout: int = 20, max_bytes: int = MAX_BYTES):
    data, content_type = fetch_bytes(url, timeout, max_bytes, exact=ARTICLE_HOST)
    encoding = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    if match:
        encoding = match.group(1)
    try:
        page = data.decode(encoding, "replace")
    except LookupError:
        page = data.decode("utf-8", "replace")
    title = article_title(page)
    parser = _ArticleText()
    try:
        parser.feed(page)
    except Exception:
        pass
    text = "\n".join(parser.parts or parser.all_parts).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return page, title, text


def image_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return ".bin"


def _attrs(raw: str) -> dict:
    return {key.lower(): value for key, value in
            re.findall(r'([A-Za-z_][\w.-]*)\s*=\s*"([^"]*)"', raw or "")}


def fetch_sticker(raw: str, timeout: int = 20, max_bytes: int = MAX_BYTES):
    """Return (bytes, ext) for a sticker CDN payload, or raise ValueError."""
    attrs = _attrs(raw)
    aeskey = attrs.get("aeskey") or ""
    urls = [attrs.get("cdnurl"), attrs.get("externurl"), attrs.get("encrypturl")]
    errors = []
    for url in urls:
        if not url:
            continue
        try:
            data, _ctype = fetch_bytes(url, timeout, max_bytes,
                                       suffixes=STICKER_HOST_SUFFIXES)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        ext = image_ext(data)
        if ext != ".bin":
            return data, ext
        keys = []
        if re.fullmatch(r"[0-9a-fA-F]{32}", aeskey):
            keys.append(bytes.fromhex(aeskey))
        if aeskey:
            keys.append(aeskey.encode("utf-8")[:16])
        for key in keys:
            try:
                decrypted = AES.new(key, AES.MODE_ECB).decrypt(data)
            except Exception:
                continue
            ext = image_ext(decrypted)
            if ext != ".bin":
                return decrypted, ext
        errors.append("下载内容不是可识别图片")
    raise ValueError(errors[-1] if errors else "没有可用的表情 URL")
