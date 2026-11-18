# Third-Party Notices

The repository is licensed under Apache-2.0 (see `LICENSE`). This file records
third-party components and reference material.

## Runtime dependencies (installed separately)

| Component | License | Use | Bundled? |
|---|---|---|---|
| `cryptography` | Apache-2.0 / BSD-3-Clause | AES, PBKDF2, HMAC | no |
| `zstandard` | BSD-3-Clause | zstd message decompression | no |
| `sqlcipher3-wheels` (optional) | zlib/libpng | open SQLCipher DB copies with WAL | no |
| `pilk` (optional) | GPL-3.0 | SILK → WAV decoding | **no** |

`pilk` is intentionally **not vendored** and is not required by default. If you
bundle `pilk` or distribute a combined work that includes it, review GPL-3.0
obligations; in that case GPL-3.0 may be the appropriate license for the
combined distribution.

## Reference projects / format research

No source code from the projects below is vendored in this repository. Their
public documentation and format descriptions informed the implementation.

| Project | License | Notes |
|---|---|---|
| `frotms/WeFlow` / `hicccc77/WeFlow` | Other (NOASSERTION) | SQLCipher/image format observations; optional WeFlow config key source |
| `sjzar/chatlog` | No license declared | V4 SQLCipher/WXGF format descriptions; no code copied |
| `0xlane/wechat-dump-rs` | Not verified | V4 key-layout research |
| `wechatauto-replica` | Apache-2.0 | `Config.Cipher` / cfgDword methodology reference |
| `tzwkb/wechat-decrypt` | MIT | Agent-skill/media-manifest design reference |
| `Tencent/wcdb` | Official Tencent project | Optional compatibility/reference only |

If you intend to copy code from a project whose license is absent or
incompatible, do not do so without a separate license review.
