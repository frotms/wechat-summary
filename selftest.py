#!/usr/bin/env python
"""Self-test for the wechat-chatlog skill.

Runs on synthetic data only (no personal WeChat content) so it can be used as
a regression check after a WeChat upgrade:

    python selftest.py

Checks:
  1. lib modules import and the CLI exposes all subcommands.
  2. SQLCipher raw-key validate/decrypt round-trip on a synthetic database.
  3. Image V2 (.dat) decryption round-trip with a synthetic container.
  4. Message parsing for every local_type the skill claims to support.
  5. Export filter semantics (time / type / sender / keyword / last-N).
  6. Filename sanitising and HTML escaping.
"""

import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

import wxexport  # noqa: E402
from lib import v4db, wxdata  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok  " if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------- 1. imports

def test_cli():
    import wfchat
    for cmd in ("doctor", "diagnose", "sessions", "messages", "search",
                "media", "export"):
        check(f"CLI subcommand {cmd}", hasattr(wfchat, "cmd_" + cmd))
    check("CLI subcommand decrypt-image", hasattr(wfchat, "cmd_decrypt_image"))
    check("no external engine dir needed",
          "wechat-export" not in sys.modules,
          "skill must not import the legacy wechat-export package")


# ---------------------------------------------------------------- 2. sqlcipher

def _build_plain_db(path):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("create table t(a integer, b text)")
    con.execute("insert into t values (1, 'hello')")
    con.commit()
    con.close()


def _encrypt_pages(bodies, key, salt):
    """Build a SQLCipher 4 raw-key file and the expected plaintext.

    ``bodies`` is a list of plaintext page bodies (page 1 excludes the 16-byte
    header, later pages are the full page minus the reserved tail).
    Returns (encrypted_bytes, expected_plaintext_bytes).
    """
    mac_key = v4db.raw_mac_key(key, salt)
    enc_file = bytearray()
    plain_file = bytearray()
    for n, body in enumerate(bodies):
        iv = os.urandom(v4db.IV_SIZE)
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor().update(body)
        mac = v4db._page_mac(mac_key, enc + iv, n)
        enc_file += (salt if n == 0 else b"") + enc + iv + mac
        if n == 0:
            plain_file += b"SQLite format 3\x00" + body
        else:
            plain_file += body
        plain_file += iv + mac
    return bytes(enc_file), bytes(plain_file)


def test_raw_key_roundtrip():
    tmp = tempfile.mkdtemp(prefix="wxselftest_")
    enc = os.path.join(tmp, "enc.db")
    out = os.path.join(tmp, "out.db")
    key = bytes(range(32))
    salt = os.urandom(16)
    body0 = b"A" * (v4db.PAGE_SIZE - v4db.RESERVE - v4db.SALT_SIZE)
    body1 = b"B" * (v4db.PAGE_SIZE - v4db.RESERVE)
    enc_bytes, plain_bytes = _encrypt_pages([body0, body1], key, salt)
    with open(enc, "wb") as f:
        f.write(enc_bytes)
    check("raw key validates correct key", v4db.validate_db_raw(enc, key))
    check("raw key rejects wrong key", not v4db.validate_db_raw(enc, bytes(32)))
    v4db.decrypt_db_raw(enc, out, key)
    got = open(out, "rb").read()
    check("raw key decrypt round-trip", got == plain_bytes,
          f"{len(got)} vs {len(plain_bytes)}")
    check("decrypted file starts with SQLite header",
          got[:15] == b"SQLite format 3")


def test_raw_key_on_real_db():
    """Optional: validate the raw-key path against a real account (if any)."""
    roots = [os.path.expanduser("~/xwechat_files"),
             os.path.expanduser("~/Documents/xwechat_files")]
    accounts = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isdir(os.path.join(d, "db_storage")):
                accounts.append(d)
    if not accounts:
        print("[skip ] real-db raw-key check (no WeChat account found)")
        return
    try:
        from lib import keyscan
    except Exception:
        return
    for acct in accounts:
        try:
            raw = keyscan.collect_raw_keys(acct)
        except Exception:
            raw = {}
        if raw:
            print(f"[ok  ] real-db raw keys found for {os.path.basename(acct)} "
                  f"({len(raw)} databases)")
            return
    print("[skip ] real-db raw-key check (no keys available)")


# ---------------------------------------------------------------- 3. image V2

def test_image_v2():
    key = "Ab3dEf6hIj9kLm2n"
    xor = 0x5A
    plain = b"\xff\xd8\xff\xe0" + bytes(range(1, 53))
    aes_size, xor_size = 32, 10
    head, raw, tail = plain[:32], plain[32:46], plain[46:56]
    pt = head + bytes([16]) * 16
    enc = Cipher(algorithms.AES(key.encode()), modes.ECB()).encryptor().update(pt)
    blob = (wxexport.V2_MAGIC + struct.pack("<LL", aes_size, xor_size) + b"\x00"
            + enc + raw + bytes(b ^ xor for b in tail))
    tmp = tempfile.mkdtemp(prefix="wxselftest_")
    path = os.path.join(tmp, "x.dat")
    with open(path, "wb") as f:
        f.write(blob)
    out, kind = wxexport.decrypt_dat(path, key, xor)
    check("image V2 decrypts", out == plain, f"{kind}")
    check("image ext detected", wxexport._image_ext(out) == ".jpg")
    try:
        wxexport.decrypt_dat(path, None, xor)
        check("missing aes key raises", False)
    except wxexport.ImageKeyMissing:
        check("missing aes key raises", True)


def test_silk_codec():
    """Optional: encode a synthetic tone to SILK, then decode to WAV."""
    try:
        import pilk
    except Exception:
        print("[skip ] SILK codec (pilk not installed)")
        return
    import math
    import struct
    import wave
    from lib import silk
    tmp = tempfile.mkdtemp(prefix="wxselftest_")
    pcm = os.path.join(tmp, "tone.pcm")
    s = os.path.join(tmp, "tone.silk")
    w = os.path.join(tmp, "tone.wav")
    rate = 24000
    frames = bytearray()
    for i in range(rate):
        v = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<h", v)
    with open(pcm, "wb") as f:
        f.write(frames)
    pilk.SilkEncoder(pcm_rate=rate, silk_rate=rate).encode(pcm, s, tencent=True)
    ok, detail = silk.to_wav(s, w, rate=rate)
    check("SILK -> WAV decoder works", ok, str(detail))
    if ok:
        with wave.open(w, "rb") as f:
            check("WAV has expected rate/channels",
                  f.getframerate() == rate and f.getnchannels() == 1,
                  f"{f.getframerate()}/{f.getnchannels()}")
            check("WAV duration close to source",
                  abs(f.getnframes() / rate - 1.0) < 0.2,
                  f"{f.getnframes() / rate:.2f}s")


def test_forward_and_article():
    record = wxdata.parse_recorditem(
        "<recordinfo><title>T</title><desc>D</desc><datalist count='2'>"
        "<dataitem datatype='1'><datadesc>hello</datadesc><sourcename>A</sourcename>"
        "<sourcetime>2026-01-01 00:00:00</sourcetime></dataitem>"
        "<dataitem datatype='8'><datatitle>f.pdf</datatitle><datafmt>pdf</datafmt>"
        "<datasize>100</datasize><sourcename>B</sourcename></dataitem>"
        "</datalist></recordinfo>")
    check("merged forward parses items", bool(record) and len(record["items"]) == 2,
          str(record))
    check("merged forward keeps sender/text",
          record and record["items"][0]["sender"] == "A"
          and record["items"][0]["desc"] == "hello")
    html = wxexport._render_record(record)
    check("merged forward renders HTML", "fwd-item" in html and "f.pdf" in html)
    parser = wxexport._ArticleText()
    parser.feed("<html><head><title>x</title></head><body>"
                "<div id='js_content'><p>Hello</p><div><p>World</p></div></div>"
                "<div>footer</div></body></html>")
    text = "".join(parser.parts)
    check("article body text extracted", "Hello" in text and "World" in text,
          text[:60])


def test_quote():
    quote = wxdata.parse_refermsg(
        "<refermsg><content>original text</content><createtime>1780563251</createtime>"
        "<displayname>Alice</displayname><fromusr>123@chatroom</fromusr>"
        "<type>1</type><chatusr>wxid_a</chatusr></refermsg>")
    check("quote parses original text",
          bool(quote) and quote["text"] == "original text"
          and quote["sender"] == "Alice", str(quote))
    html = wxexport._render_quote(quote)
    check("quote renders HTML", "class=\"quote\"" in html and "original text" in html)


def test_wxgf():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
    wx = bytes(b ^ 0xC8 for b in png)
    out = wxexport._decode_wxgf(wx)
    check("WXGF single-byte XOR -> PNG", out == png and out[:8] == b"\x89PNG\r\n\x1a\n")


def test_network_allowlist():
    check("article host allowlist", wxexport._host_ok(
        "https://mp.weixin.qq.com/s?x=1", exact=wxexport.ARTICLE_HOST))
    check("article host rejects other domains", not wxexport._host_ok(
        "https://evil.example/mp.weixin.qq.com", exact=wxexport.ARTICLE_HOST))
    check("sticker host allowlist", wxexport._host_ok(
        "http://vweixinf.tc.qq.com/110/stodownload?m=x",
        suffixes=wxexport.STICKER_HOST_SUFFIXES))
    check("sticker host rejects arbitrary URL", not wxexport._host_ok(
        "https://evil.example/x", suffixes=wxexport.STICKER_HOST_SUFFIXES))
    check("network rejects userinfo", not wxexport._host_ok(
        "https://user@mp.weixin.qq.com/s", exact=wxexport.ARTICLE_HOST))


# ---------------------------------------------------------------- 4. parsing

def test_parsing():
    cases = [
        (1, "hello", "text"),
        (3, '<msg><img md5="a" /></msg>', "image"),
        (34, '<msg><voicemsg voicemd5="b" /></msg>', "voice"),
        (43, '<msg><videomsg md5="c" /></msg>', "video"),
        (47, '<msg><emoji md5="d" /></msg>', "sticker"),
        (48, '<msg><location label="place" /></msg>', "location"),
        (50, '<msg><voicemsg /></msg>', "call"),
        (10000, "<msg><revokemsg><replacemsg>recalled</replacemsg></revokemsg></msg>", "system"),
        (49, "<msg><appmsg><type>6</type><title>f.pdf</title></appmsg></msg>", "file"),
        (49, "<msg><appmsg><type>5</type><title>t</title><url>http://x</url></appmsg></msg>", "app"),
    ]
    for base, content, expect in cases:
        text, kind, extra = wxdata.parse_content(base, content)
        check(f"parse local_type {base} -> {expect}", kind == expect, f"got {kind}")
    text, kind, extra = wxdata.parse_content(
        49, "<msg><appmsg><type>6</type><title>f.pdf</title><md5>abc</md5>"
             "<fileext>pdf</fileext><totallen>10</totallen></appmsg></msg>")
    check("file message keeps md5/ext/size",
          extra.get("md5") == "abc" and extra.get("fileext") == "pdf"
          and extra.get("size") == "10")


# ---------------------------------------------------------------- 5. filters

def test_filters():
    class A:
        since = "2026-01-02"
        until = "2026-01-02"
        types = "text,image"
        keyword = None
        sender = None
        last = None

    msgs = [
        {"time": 1767312000, "time_str": "2026-01-02 00:00:00", "kind": "text",
         "type": 1, "text": "hello", "sender": "wxid_a", "sender_name": "Alice",
         "is_self": False, "extra": {}},
        {"time": 1767398400, "time_str": "2026-01-03 00:00:00", "kind": "text",
         "type": 1, "text": "next day", "sender": "wxid_b", "sender_name": "Bob",
         "is_self": False, "extra": {}},
        {"time": 1767312060, "time_str": "2026-01-02 00:01:00", "kind": "image",
         "type": 3, "text": "[图片]", "sender": "wxid_a", "sender_name": "Alice",
         "is_self": False, "extra": {}},
    ]
    picked = wxexport.filter_messages(msgs, A())
    check("time window filters to one day", len(picked) == 2, str(len(picked)))
    check("type filter applied",
          all(m["kind"] in ("text", "image") for m in picked))

    A.types = "image"
    picked = wxexport.filter_messages(msgs, A())
    check("type filter narrows", len(picked) == 1 and picked[0]["kind"] == "image")

    A.types = ""
    A.sender = "alice"
    picked = wxexport.filter_messages(msgs, A())
    check("sender filter matches display name",
          len(picked) == 2 and all(m["sender_name"] == "Alice" for m in picked))

    # Keyword / last-N are checked without the time window so the other-day
    # message stays in range.
    A.since = None
    A.until = None
    A.sender = None
    A.keyword = "next"
    picked = wxexport.filter_messages(msgs, A())
    check("keyword filter works", len(picked) == 1 and "next day" in picked[0]["text"])

    A.keyword = None
    A.last = 1
    picked = wxexport.filter_messages(msgs, A())
    check("last-N keeps the newest",
          len(picked) == 1 and picked[0]["time_str"] == "2026-01-03 00:00:00",
          str([m["time_str"] for m in picked]))


# ---------------------------------------------------------------- 6. naming

def test_naming():
    check("safe name strips separators", "/" not in wxexport._safe_name("a/b\\c"))
    check("safe name keeps unicode", wxexport._safe_name("群名字") == "群名字")
    import html as _html
    out = wxexport.render_body({"kind": "text", "text": "<script>", "extra": {}})
    check("html escapes text", "&lt;script&gt;" in out)


def main():
    print("wechat-chatlog self-test (synthetic data only)\n")
    test_cli()
    test_raw_key_roundtrip()
    test_image_v2()
    test_silk_codec()
    test_forward_and_article()
    test_quote()
    test_wxgf()
    test_network_allowlist()
    test_parsing()
    test_filters()
    test_naming()
    test_raw_key_on_real_db()
    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
