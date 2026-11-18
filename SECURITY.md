# Security & Privacy Notes

This skill is intentionally **read-only with respect to WeChat**. It never sends,
forwards, recalls, deletes, edits, or writes messages.

## What it reads

- Copies of `db_storage/*.db` (and `-wal`/`-shm`) under
  `~/xwechat_files/<account>/`.
- Local media: `msg/`, `cache/`, `emoticon/`, `business/`.
- Optional read-only process memory (`OpenProcess`, `ReadProcessMemory`,
  `VirtualQueryEx`) to obtain decryption keys for the currently logged-in
  account. It does **not** inject, hook, patch, or modify `Weixin.exe`.

## Key handling

- Keys are used in memory only.
- Keys are never printed to stdout/stderr.
- Keys are never written to disk by this skill.
- Optional `keys.json` / `--key-file` / `WECHAT_DB_KEY` are user-managed inputs.
- `doctor` reports only key status/length, not key material.

## Network

Network is **off by default**. Two explicit opt-ins exist:

- `--fetch-articles`: fetch only `mp.weixin.qq.com` article pages (GET only).
- `--download-stickers`: fetch sticker URLs from message XML, restricted to
  Tencent media hosts (`*.tc.qq.com`, `*.qpic.cn`, `*.weixin.qq.com`,
  `*.wechat.com`, `*.wx.qq.com`).

Both follow the same rule:

- HTTPS/HTTP GET only, no upload of chat content;
- host allowlist checked before the request and again after redirects;
- URL userinfo (`user@host`) rejected;
- response size capped;
- no cookies or account credentials are sent.

## Local artifacts

- Decrypted database copies and temporary WAV/HTML files live under
  `$WECHAT_WORK_DIR` (default `~/.wechat-chatlog/work`).
- Exports contain plaintext chat content and attachments.
- Use `python wfchat.py clean` to remove the work directory.
- `clean` uses normal file deletion, not secure erase; use disk encryption or a
  dedicated volume for highly sensitive data.
- Do not commit `keys.json`, work directories, exports, or real chat data.

## Logging

- Progress lines include counts, titles, and media status only.
- Message bodies and key material are not written to stderr by key acquisition.
- `manifest.json` intentionally contains titles/excerpts and media paths; treat
  it as sensitive.

## Verification

- Export-time `manifest.checks` and `wfchat.py verify --deep` help detect
  missing messages/media, but only within the selected scope and local data.
- Local-only checks cannot prove that data never synced to this PC exists.
