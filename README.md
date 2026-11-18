# wechat-summary

Read-only WeChat 4.x chat export skill for Claude Code.

It decrypts **copies** of the local SQLCipher databases, parses messages and media,
and writes HTML / JSON / Markdown / TXT exports. It never sends, forwards, recalls,
deletes, edits, or modifies WeChat data, and it never uses screenshots/OCR/UI automation.

## Features

- Text, images, videos, files, links, official-account messages
- Chat voice: raw SILK + playable WAV (optional `pilk`)
- Stickers: local cache + optional Tencent CDN download
- Official-account article body: optional `--fetch-articles`
- Merged-forward inner text/file names (local media copied when cached)
- Quote messages: original sender/time/text restored
- WXGF images: single-byte XOR types converted to PNG/JPEG/BMP
- Filters: time range, group, private chat, sender, keyword, message types, last N
- Batch export with `--all`
- Completeness checks: `manifest.checks`, `verify [--deep]`
- Read-only key acquisition: optional `keys.json` / WeFlow config / in-memory scan
- No GUI, no cross-platform support (Windows only by design)

## Requirements

- Windows 10/11, Python 3.10+
- `cryptography`, `zstandard`
- Optional: `sqlcipher3-wheels` (WAL), `pilk` (SILK→WAV)

```bash
python -m pip install -r requirements.txt
# optional:
python -m pip install sqlcipher3-wheels pilk
```

## Quick start

```bash
python wfchat.py doctor
python wfchat.py --account <account-dir> diagnose
python wfchat.py sessions --limit 20

python wfchat.py --account <account-dir> export \
  --session "<group-or-contact>" \
  --since 2026-11-01 --until 2026-11-30 \
  --types text,image,video,file,voice \
  --format html,json,md,txt \
  --out D:\backup\chat
```

Optional network features are off by default:

```bash
# official-account article text/html
... --fetch-articles

# stickers missing locally
... --download-stickers
```

## Verify completeness

```bash
python wfchat.py verify --out D:\backup\chat
python wfchat.py --account <account-dir> verify --out D:\backup\chat --deep
```

`--deep` re-reads the source DB and compares message `local_id + server_id`;
it reports `missing_ids` / `extra_ids`.

## Regression

```bash
python selftest.py                 # synthetic data only
python regression.py --account <account-dir> --last 30
```

## Privacy

- Keys are never printed or written to disk.
- Decrypted copies live under `~/.wechat-chatlog/work` (or `WECHAT_WORK_DIR`).
- Exports are plaintext; treat them as sensitive.
- `python wfchat.py clean` removes the work directory (normal delete, not secure erase).
- See `SECURITY.md` and `THIRD_PARTY_NOTICES.md`.

## Status / TODO

Current features and known gaps are kept in [`TODO.md`](TODO.md).
Version-upgrade procedures: [`VERSION_MAINTENANCE.md`](VERSION_MAINTENANCE.md).
Reference projects (not runtime dependencies): [`REFERENCES.md`](REFERENCES.md).

## License

Apache-2.0. See `LICENSE` and `THIRD_PARTY_NOTICES.md`.
`pilk` is optional, not vendored, and GPL-3.0; do not bundle it without a license
review.
