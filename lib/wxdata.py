"""WeChat 4.x SQLCipher data access for the wechat-chatlog skill.

Self-contained: only Python crypto libs (and optional sqlcipher3 for WAL).
WeChat's own files are never modified; plaintext copies live under WORK.
"""

import datetime
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET

import zstandard as zstd

try:
    from . import v4db
except ImportError:  # direct script execution
    import v4db  # noqa: E402

WEFLOW_CONFIG = os.path.expanduser("~/AppData/Roaming/weflow/WeFlow-config.json")
WEFLOW_USERDATA = os.path.expanduser("~/AppData/Roaming/weflow")
WORK = os.environ.get(
    "WECHAT_WORK_DIR",
    os.path.join(os.path.expanduser("~"), ".wechat-chatlog", "work"),
)

_ZSTD = zstd.ZstdDecompressor()


# ---------------------------------------------------------------- data access

def decompress(data, ctype):
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if ctype:
        try:
            data = _ZSTD.decompress(data)
        except Exception:
            pass
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


class NoMessages(Exception):
    """The session has no message table in any message shard."""


class WeChatData:
    def __init__(self, account_dir, passphrase, work=WORK, raw_keys=None):
        self.account_dir = account_dir
        self.key = passphrase
        self.raw_keys = raw_keys or {}
        self.work = work
        # Namespace plaintext copies per account.  A shared out/ directory
        # previously let one account's cached session.db shadow another's.
        self.out = os.path.join(work, "out", os.path.basename(account_dir.rstrip("/\\")))
        os.makedirs(self.out, exist_ok=True)
        self.conns = {}
        self.live_conns = {}
        self.contacts = {}  # username -> (remark, nickname)
        self.sender_names = {}  # (msgdb, sender_id) -> username
        self._shards = None
        self._own = None

    def db(self, module, name):
        rel = os.path.join(module, name)
        rel_key = module + "/" + name
        if rel in self.conns:
            return self.conns[rel]
        raw = self.raw_keys.get(rel_key)
        if raw:
            # Live copy + SQLCipher so -wal/-shm frames are applied too.
            conn = self._db_live(module, name, rel_key, raw)
            self.conns[rel] = conn
            return conn
        src = os.path.join(self.account_dir, "db_storage", rel)
        dst = os.path.join(self.out, module + "_" + name)
        if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
            if not self.key:
                raise ValueError(f"no raw key for {rel_key}")
            if not v4db.validate_db(src, self.key):
                raise ValueError(f"stored key does not validate against {src}")
            v4db.decrypt_db(src, dst, self.key)
        conn = sqlite3.connect(dst)
        conn.row_factory = sqlite3.Row
        self.conns[rel] = conn
        return conn

    def _db_live(self, module, name, rel_key, raw_key):
        if rel_key in self.live_conns:
            return self.live_conns[rel_key]
        import shutil
        src = os.path.join(self.account_dir, "db_storage", module, name)
        live_dir = os.path.join(self.out, "live")
        os.makedirs(live_dir, exist_ok=True)
        dst = os.path.join(live_dir, module + "_" + name)
        try:
            import sqlcipher3
        except ImportError:
            if not v4db.validate_db_raw(src, raw_key):
                raise ValueError(f"stored raw key does not validate against {src}")
            v4db.decrypt_db_raw(src, dst, raw_key)
            conn = sqlite3.connect(dst)
            conn.row_factory = sqlite3.Row
            self.live_conns[rel_key] = conn
            return conn
        for ext in ("", "-wal", "-shm"):
            s = src + ext
            if os.path.isfile(s):
                shutil.copyfile(s, dst + ext)
        conn = sqlcipher3.connect(dst)
        conn.row_factory = sqlcipher3.Row
        conn.execute("PRAGMA key = \"x'%s'\"" % raw_key.hex())
        conn.execute("PRAGMA cipher_page_size = 4096")
        conn.execute("select count(*) from sqlite_master").fetchone()
        self.live_conns[rel_key] = conn
        return conn

    def load_contacts(self):
        if self.contacts:
            return
        c = self.db("contact", "contact.db")
        for r in c.execute("select username, remark, nick_name, alias from contact"):
            self.contacts[r["username"]] = (
                r["remark"] or "",
                r["nick_name"] or "",
                r["alias"] or "",
            )

    def display_name(self, username):
        if not username:
            return "未知"
        self.load_contacts()
        remark, nick, alias = self.contacts.get(username, ("", "", ""))
        return remark or nick or alias or username

    def session_row(self, username):
        c = self.db("session", "session.db")
        return c.execute("select * from SessionTable where username=?", (username,)).fetchone()

    def message_shards(self):
        """All message shards (regular + official-account), opened once."""
        if self._shards is None:
            shard_dir = os.path.join(self.account_dir, "db_storage", "message")
            names = sorted(
                n for n in os.listdir(shard_dir) if re.fullmatch(r"(biz_)?message_\d+\.db", n)
            )
            self._shards = [(self.db("message", n), n) for n in names]
        return self._shards

    def find_message_tables(self, username):
        """A session can be split across several shards; return every (conn, table)."""
        table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
        found = []
        for conn, _name in self.message_shards():
            if conn.execute(
                "select 1 from sqlite_master where type='table' and name=?", (table,)
            ).fetchone():
                found.append((conn, table))
        if not found:
            raise NoMessages(username)
        return found

    def sender_name(self, conn, sender_id):
        key = (id(conn), sender_id)
        if key not in self.sender_names:
            r = conn.execute("select user_name from Name2Id where rowid=?", (sender_id,)).fetchone()
            self.sender_names[key] = r[0] if r else ""
        return self.sender_names[key]

    def own_username(self):
        """Identify the account's own wxid from 1:1 chats.

        In a private chat ``<contact>``, the sender that is not the contact is
        the account itself.  This is more reliable than counting all senders
        (the old heuristic could pick a contact, or a stale WeFlow myWxid).
        """
        if self._own is not None:
            return self._own
        counts = {}
        chats = 0
        cur = self.db("session", "session.db").execute(
            "select username from SessionTable "
            "where username not like '%@chatroom' and username not like 'gh_%' limit 400"
        )
        for (username,) in cur:
            try:
                tables = self.find_message_tables(username)
            except NoMessages:
                continue
            senders = set()
            for conn, table in tables:
                for (sid,) in conn.execute(f'select distinct real_sender_id from "{table}"'):
                    name = self.sender_name(conn, sid)
                    if name:
                        senders.add(name)
            others = senders - {username}
            if len(others) == 1:
                chats += 1
                other = next(iter(others))
                counts[other] = counts.get(other, 0) + 1
        if chats and counts:
            best, n = max(counts.items(), key=lambda kv: kv[1])
            if n >= max(1, int(chats * 0.6)):
                self._own = best
        if self._own is None:
            # Optional legacy hint only; never required for the skill to work.
            try:
                if os.path.isfile(WEFLOW_CONFIG):
                    cfg = json.load(open(WEFLOW_CONFIG, encoding="utf-8"))
                    hint = cfg.get("myAccountId") or cfg.get("myWxid") or ""
                    base = os.path.basename(self.account_dir.rstrip("/\\"))
                    for cand in (base, hint):
                        if cand and cand in counts:
                            self._own = cand
                            break
            except Exception:
                pass
        if self._own is None:
            base = os.path.basename(self.account_dir.rstrip("/\\"))
            self._own = base.rsplit("_", 1)[0] if "_" in base else base
        return self._own


# ---------------------------------------------------------------- message text

_TAG_RE = re.compile(r"<([A-Za-z_][\w.-]*)[^>]*?>(.*?)</\1>", re.S)


def xml_field(xml, tag):
    m = re.search(rf"<{tag}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{tag}>", xml, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def xml_attr(xml, name):
    m = re.search(rf'{re.escape(name)}="([^"]*)"', xml)
    return html.unescape(m.group(1).strip()) if m else ""


def parse_recorditem(xml):
    """Parse a merged-forward <recorditem> into a small, renderable structure."""
    if not xml:
        return None
    try:
        root = ET.fromstring("<root>" + xml + "</root>")
    except Exception:
        return None
    info = root.find("recordinfo")
    if info is None:
        info = root
    data = {
        "title": (info.findtext("title") or "").strip(),
        "desc": (info.findtext("desc") or "").strip(),
        "items": [],
    }
    for di in root.iter("dataitem"):
        def txt(tag):
            return (di.findtext(tag) or "").strip()
        item = {
            "datatype": di.get("datatype") or txt("datatype"),
            "dataid": di.get("dataid") or "",
            "title": txt("datatitle"),
            "desc": txt("datadesc"),
            "sender": txt("sourcename"),
            "time": txt("sourcetime"),
            "fmt": txt("datafmt"),
            "size": txt("datasize"),
            "cdn_url": txt("cdndataurl"),
            "cdn_key": txt("cdndatakey"),
        }
        if item["title"] or item["desc"]:
            data["items"].append(item)
    if not data["items"]:
        return None
    return data


def parse_refermsg(content):
    """Parse a quote/refer message (appmsg type 57) into original-message info."""
    if not content:
        return None
    m = re.search(r"<refermsg>(.*?)</refermsg>", content, re.S)
    if not m:
        return None
    block = m.group(1)
    qtype = xml_field(block, "type") or ""
    qcontent = xml_field(block, "content")
    display = xml_field(block, "displayname")
    fromusr = xml_field(block, "fromusr")
    chatusr = xml_field(block, "chatusr")
    ctime = xml_field(block, "createtime")
    try:
        ctime_s = datetime.datetime.fromtimestamp(int(ctime)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ctime_s = ctime
    text = qcontent
    kind = "text"
    extra = {}
    try:
        base = int(qtype)
    except ValueError:
        base = 0
    if base == 3:
        kind = "image"
        md5 = xml_field(qcontent, "md5") or xml_attr(qcontent, "md5")
        extra["md5"] = md5
        text = "[图片]"
    elif base == 34:
        kind = "voice"
        text = "[语音]"
    elif base == 43:
        kind = "video"
        text = "[视频]"
    elif base == 47:
        kind = "sticker"
        text = "[表情]"
    elif base == 49:
        kind = "app"
        app_type = xml_field(qcontent, "type")
        title = xml_field(qcontent, "title")
        des = xml_field(qcontent, "des")
        url = xml_field(qcontent, "url")
        extra.update({"app_type": app_type, "title": title, "url": url})
        text = " ".join(x for x in (title, des) if x) or "[应用消息]"
    return {
        "type": base,
        "kind": kind,
        "sender": display or chatusr or fromusr,
        "fromusr": fromusr,
        "chatusr": chatusr,
        "time": ctime_s,
        "text": text,
        "extra": extra,
    }


def parse_content(base_type, content):
    """Return (text, kind, extra dict) for a message."""
    if base_type == 1:
        return content, "text", {}
    if base_type == 3:
        md5 = xml_field(content, "md5") or xml_attr(content, "md5")
        if not md5:
            m = re.search(r'md5="([0-9a-f]{32})"', content)
            md5 = m.group(1) if m else ""
        return "[图片]", "image", {"md5": md5}
    if base_type == 34:
        return "[语音]", "voice", {
            "md5": xml_attr(content, "voicemd5"),
            "length": xml_attr(content, "voicelength"),
            "format": xml_attr(content, "voiceformat"),
        }
    if base_type == 43:
        return "[视频]", "video", {
            "md5": xml_attr(content, "md5"),
            "new_md5": xml_attr(content, "newmd5"),
            "raw_md5": xml_attr(content, "rawmd5"),
            "length": xml_attr(content, "playlength"),
        }
    if base_type == 47:
        return "[表情]", "sticker", {
            "md5": xml_attr(content, "md5"),
            "extern_md5": xml_attr(content, "externmd5"),
            "desc": xml_attr(content, "desc"),
            "cdnurl": xml_attr(content, "cdnurl"),
            "encrypturl": xml_attr(content, "encrypturl"),
            "externurl": xml_attr(content, "externurl"),
            "thumburl": xml_attr(content, "thumburl"),
            "aeskey": xml_attr(content, "aeskey"),
            "filekey": xml_attr(content, "filekey"),
            "productid": xml_attr(content, "productid"),
            "designerid": xml_attr(content, "designerid"),
        }
    if base_type == 42:
        return "[名片] " + (xml_field(content, "nickname") or ""), "card", {}
    if base_type == 48:
        return "[位置] " + (xml_field(content, "label") or ""), "location", {}
    if base_type == 50:
        return "[通话]", "call", {}
    if base_type == 49:
        app_type = xml_field(content, "type")
        title = xml_field(content, "title")
        des = xml_field(content, "des")
        url = xml_field(content, "url")
        labels = {
            "5": "[链接]", "6": "[文件]", "19": "[合并转发]", "33": "[小程序]",
            "36": "[小程序]", "57": "[引用]", "2000": "[转账]", "2001": "[红包]",
            "2003": "[红包]", "62": "[视频号]", "87": "[群公告]", "115": "[直播]",
        }
        label = labels.get(app_type, "[应用消息]")
        extra = {"app_type": app_type, "title": title, "url": url, "des": des}
        if app_type == "6":
            # 文件消息：md5/title/fileext/totallen 都在 appattach 里。
            extra.update({
                "md5": xml_field(content, "md5") or xml_attr(content, "md5"),
                "fileext": xml_field(content, "fileext"),
                "size": xml_field(content, "totallen"),
            })
        elif app_type == "19":
            extra["recorditem"] = xml_field(content, "recorditem")
            extra["record"] = parse_recorditem(extra["recorditem"])
        elif app_type == "57":
            extra["quote"] = parse_refermsg(content)
        body = " ".join(x for x in (title, des) if x)
        text = f"{label} {body}".strip()
        if app_type == "57" and extra.get("quote"):
            q = extra["quote"]
            text = f"[引用 {q.get('sender') or ''}] {q.get('text') or ''}\n{title}"
        if url and app_type in ("5", "19"):
            text += f"\n{url}"
        return text, ("file" if app_type == "6" else "app"), extra
    if base_type == 10000:
        revoke = re.search(r"<revokemsg>.*?<replacemsg>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</replacemsg>", content, re.S)
        if revoke:
            return f"[系统] {html.unescape(revoke.group(1))}", "system", {}
        sysmsg = re.search(r"<content>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content>", content, re.S)
        if sysmsg:
            return f"[系统] {html.unescape(sysmsg.group(1))}", "system", {}
        return "[系统消息]", "system", {}
    if base_type == 10002:
        return "[系统消息]", "system", {}
    return f"[消息类型 {base_type}]", "unknown", {}


# ---------------------------------------------------------------- render

def fmt_time(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return str(ts)


def collect(data, username, since=None, until=None):
    sess = data.session_row(username)
    if sess is None:
        raise SystemExit(f"session not found: {username}")
    tables = data.find_message_tables(username)
    is_group = username.endswith("@chatroom")
    title = data.display_name(username)
    own = data.own_username()
    messages = []
    merged = []
    where = ""
    params = ()
    if since is not None or until is not None:
        clauses = []
        if since is not None:
            clauses.append("create_time >= ?")
        if until is not None:
            clauses.append("create_time < ?")
        where = " where " + " and ".join(clauses)
        params = tuple(x for x in (since, until) if x is not None)
    for conn, table in tables:
        for r in conn.execute(f'select * from "{table}"{where}', params):
            merged.append((r["sort_seq"], r["local_id"], conn, r))
    merged.sort(key=lambda x: (x[0], x[1]))
    seen = set()
    for _seq, _lid, conn, r in merged:
        srv = r["server_id"]
        if srv and str(srv) != "0":
            dedup = ("srv", str(srv))
        else:
            raw = r["message_content"]
            dedup = (
                "row",
                r["sort_seq"],
                r["local_id"],
                r["real_sender_id"],
                r["local_type"],
                hash(bytes(raw)) if isinstance(raw, (bytes, bytearray)) else str(raw),
            )
        if dedup in seen:
            continue
        seen.add(dedup)
        base = r["local_type"] & 0xFFFFFFFF
        content = decompress(r["message_content"], r["WCDB_CT_message_content"])
        sender = data.sender_name(conn, r["real_sender_id"])
        if base == 1 and is_group:
            m = re.match(r"^(wxid_[^:\s]+|[^:\s]+@(chatroom|openim)):\s*\n?", content)
            if m:
                sender = sender or m.group(1)
                content = content[m.end():]
        text, kind, extra = parse_content(base, content)
        messages.append(
            {
                "local_id": r["local_id"],
                "server_id": r["server_id"],
                "time": r["create_time"],
                "time_str": fmt_time(r["create_time"]),
                "type": base,
                "kind": kind,
                "sender": sender,
                "sender_name": data.display_name(sender) if sender else ("我" if sender == own else ""),
                "is_self": bool(own and sender == own),
                "text": text,
                "extra": extra,
            }
        )
    meta = {
        "account": os.path.basename(data.account_dir),
        "username": username,
        "title": title,
        "is_group": is_group,
        "message_count": len(messages),
        "first_time": messages[0]["time_str"] if messages else None,
        "last_time": messages[-1]["time_str"] if messages else None,
        "summary": sess["summary"],
    }
    return meta, messages


_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#ededed;font:15px/1.5 "Microsoft YaHei",system-ui,sans-serif;color:#222}}
header{{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:12px 18px}}
header h1{{font-size:17px;margin:0 0 4px}}
header .sub{{color:#888;font-size:12px}}
main{{max-width:860px;margin:0 auto;padding:16px 18px 60px}}
.day{{text-align:center;color:#999;font-size:12px;margin:18px 0 10px}}
.m{{display:flex;margin:8px 0}}
.m.self{{justify-content:flex-end}}
.avatar{{width:34px;height:34px;border-radius:4px;background:#c9d6e8;color:#456;display:flex;
 align-items:center;justify-content:center;font-size:13px;flex:0 0 34px;margin:0 8px;user-select:none}}
.bwrap{{max-width:72%}}
.meta{{font-size:11px;color:#999;margin:0 2px 3px}}
.m.self .meta{{text-align:right}}
.bubble{{background:#fff;border-radius:6px;padding:8px 11px;white-space:pre-wrap;word-break:break-word;
 box-shadow:0 1px 1px rgba(0,0,0,.06)}}
.m.self .bubble{{background:#95ec69}}
.sys{{text-align:center;color:#999;font-size:12px;margin:12px 0;white-space:pre-wrap}}
.kind{{color:#7a7a7a;font-style:italic}}
a{{color:#2a6cb8}}
</style></head><body>
<header><h1>{title}</h1>
<div class="sub">{username} · {message_count} 条消息 · {first_time} ~ {last_time}</div></header>
<main>{body}</main></body></html>
"""


def render(meta, messages):
    out = []
    last_day = None
    for m in messages:
        day = m["time_str"][:10]
        if day != last_day:
            out.append(f'<div class="day">{day}</div>')
            last_day = day
        if m["kind"] == "system":
            out.append(f'<div class="sys">{html.escape(m["text"])}</div>')
            continue
        cls = "m self" if m["is_self"] else "m"
        who = "我" if m["is_self"] else (m["sender_name"] or m["sender"] or "未知")
        initial = html.escape(who[:1])
        if m["kind"] in ("text",):
            bubble = html.escape(m["text"])
        else:
            bubble = f'<span class="kind">{html.escape(m["text"])}</span>'
            if m["kind"] == "app" and m["extra"].get("url"):
                url = html.escape(m["extra"]["url"], quote=True)
                bubble += f'<br><a href="{url}">{url}</a>'
        meta_line = f"{html.escape(who)} · {m['time_str'][11:16]}"
        out.append(
            f'<div class="{cls}"><div class="avatar">{initial}</div>'
            f'<div class="bwrap"><div class="meta">{meta_line}</div>'
            f'<div class="bubble">{bubble}</div></div></div>'
        )
    fields = {k: html.escape(str(v)) for k, v in meta.items()}
    fields["body"] = "\n".join(out)
    return _HTML.format(**fields)
