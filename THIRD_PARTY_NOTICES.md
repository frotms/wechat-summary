# Third-Party Notices

本仓库根许可证为 **MIT**（见 `LICENSE`）。本文件记录第三方代码、依赖和参考资料。

## 1. Vendored 代码

| 组件 | 许可证 | 用途 | 位置 |
| --- | --- | --- | --- |
| [TangGod991021/wechat-exporter](https://github.com/TangGod991021/wechat-exporter) | MIT | 4.2 key 提取、SQLCipher 解密、消息解析、媒体读取 | `scripts/vendor/wechat42/` |

- 上游 commit：`cc718ef14fd815c9d04c2035b53cee0ee0539e36`
- 复制日期：2026-11-17
- 修改：把 `app.core.*` 导入改为包内相对导入，便于在本 skill 内运行；其余保留上游实现。
- 许可证和 notice 见同目录 `LICENSE` / `NOTICE.md`。

## 2. 运行时依赖（单独安装，不 vendored）

| 组件 | 许可证 | 用途 | 是否必需 |
| --- | --- | --- | --- |
| Node.js | MIT | 编排、打包、HTML/Markdown | 必需 |
| Python | PSF-2.0 | 原生读取、媒体、audit | 必需 |
| `cryptography` | Apache-2.0 OR BSD-3-Clause | AES/HMAC/PBKDF2 | 必需 |
| `zstandard` | BSD-3-Clause | 消息 zstd 解压 | 必需 |
| `sqlcipher3-wheels` | zlib/libpng | 打开带 WAL 的 SQLCipher 副本 | 4.1/4.2 原生需要 |
| `silk-python` | BSD | SILK → WAV | 语音功能需要 |
| `imageio-ffmpeg` | BSD-2-Clause（包本身） | 提供 ffmpeg，转码表情 `wxgf`/H.265 | 表情功能需要 |

> `imageio-ffmpeg` 包本身是 BSD-2-Clause，但它在运行时会提供/下载 **ffmpeg 可执行文件**；
> ffmpeg 的许可证以该二进制为准（常见为 LGPL-2.1+，部分构建含 GPL 组件）。本项目不修改、
> 不重新分发 ffmpeg 源码，只调用其命令行。分发包含 ffmpeg 的完整包前请单独复核。
>
> 本 skill **不使用** `pilk`（GPL-3.0）；语音使用 BSD 的 `silk-python`。

## 3. 可选 / legacy 依赖

| 组件 | 许可证 | 用途 |
| --- | --- | --- |
| [zhuobichen/weflow-cli](https://github.com/zhuobichen/weflow-cli) | MIT | `--legacy-weflow` 回退读取/导出 |
| [Tencent/wcdb](https://github.com/Tencent/wcdb) | 官方项目（BSD-3-Clause 等） | 兼容性参考；未接入运行时 |

`weflow-cli` 不是默认依赖；4.2 原生路径不需要它。

## 4. 仅参考、未复制的项目

以下项目只用于格式/思路参考，**没有源码进入本仓库**：

| 项目 | 许可证/备注 |
| --- | --- |
| `hicccc77/WeFlow` | CC BY-NC-SA 4.0；仅设计参考，未 vendor |
| `sjzar/chatlog` | 仓库已清空；历史格式参考 |
| `0xlane/wechat-dump-rs` | key 布局研究参考 |
| `wechatauto-replica` | Apache-2.0；cfgDword/图片格式方法论参考 |
| `tzwkb/wechat-decrypt` | MIT；Agent-skill/媒体清单设计参考 |
| `mofan6/Moect` | GPL-3.0；仅行为/格式参考，未复制代码 |
| `LC044/WeChatMsg`、`xaoyaoo/PyWxDump`、`git-jiadong/wechatDataBackup` | 历史工具；公开页多已清空，不作依赖 |

完整清单和用途见 [`REFERENCES.md`](REFERENCES.md)。

## 5. 文档来源说明

本仓库的 `SECURITY.md`、`VERSION_MAINTENANCE.md`、`REFERENCES.md` 等文档在结构上参考了
归档分支 `main` 的同名文档（Apache-2.0，同一作者），并已按 4.2 架构改写。归档内容保留在 tag
`archive-main-python-4.1-2026-11-18`，不再作为运行时依赖。

## 6. 分发注意

- 不要把 `work/`、`sandbox/`、`home/`、`keys.json`、导出物打进分发包。
- 如果分发包含 `imageio-ffmpeg` 的 ffmpeg 二进制，复核该二进制的许可证和源码提供义务。
- 如果未来引入 GPL 组件，需要单独评估整个分发物的许可证；当前根许可证为 MIT。
