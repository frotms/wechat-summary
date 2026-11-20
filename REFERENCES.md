# 参考项目清单（仅参考，不作为运行时依赖）

记录日期：2026-11-19（微信 4.2.0.13 基线）  
用途：微信升级到 4.3+ 时，快速回看这些项目有没有新的 key 定位、SQLCipher 参数、DB schema、图片/视频格式的处理思路。

> 重要：除 `scripts/vendor/wechat42/` 外，本 skill **不 import、不调用、不打包**下列项目。
> 它们只作为逆向/实现参考；使用前确认仓库是否仍可用、许可证、以及与当前微信版本是否匹配。
> 不要直接运行来源不明的二进制或 DLL。

## 1. 运行时直接用到的项目

| 项目 | 许可证 | 用途 | 是否 vendored |
| --- | --- | --- | --- |
| [TangGod991021/wechat-exporter](https://github.com/TangGod991021/wechat-exporter) | MIT | 4.2 key 提取、SQLCipher 解密、消息/媒体解析 | **是**，`scripts/vendor/wechat42/`，上游 commit `cc718ef14fd815c9d04c2035b53cee0ee0539e36` |
| [zhuobichen/weflow-cli](https://github.com/zhuobichen/weflow-cli) | MIT | `--legacy-weflow` 回退读取/导出 | 否，可选安装 |
| [Tencent/wcdb](https://github.com/Tencent/wcdb) | 官方项目 | SQLCipher/WCDB 语义与兼容性参考 | 否，仅参考 |
| `sqlcipher3-wheels` | zlib/libpng | 打开带 WAL 的 SQLCipher 副本（4.1/4.2 原生） | 否 |
| `cryptography` | Apache-2.0 / BSD-3-Clause | AES/HMAC/PBKDF2 | 否 |
| `zstandard` | BSD-3-Clause | 消息 zstd 解压 | 否 |
| `silk-python` | BSD | SILK → WAV | 否 |
| `imageio-ffmpeg` | BSD-2-Clause | 表情 `wxgf`/H.265 → PNG/GIF | 否 |

## 2. 调研过的 GitHub 项目

| 项目 | 约 Stars（2026-11-16） | 4.x | 可参考的点 | 注意事项 |
|---|---:|---|---|---|
| [hicccc77/WeFlow](https://github.com/hicccc77/WeFlow) | 16.1k | ✅ | 图片/视频解密、导出格式、HTTP API | Electron/GUI；CC BY-NC-SA 4.0；不能直接当库 |
| [LC044/WeChatMsg](https://github.com/LC044/WeChatMsg) | 42.4k | 曾支持 | 历史 4.0 适配、HTML/Word/CSV | 公开页已基本清空，不能依赖 |
| [sjzar/chatlog](https://github.com/sjzar/chatlog) | 9.2k | 曾支持 | HTTP/MCP、key 提取思路 | 仓库只剩 README，作者停止支持 |
| [xaoyaoo/PyWxDump](https://github.com/xaoyaoo/PyWxDump) | 9.6k | 3.x | 内存扫描/解密历史 | 仓库描述为“删库”，不可用 |
| [git-jiadong/wechatDataBackup](https://github.com/git-jiadong/wechatDataBackup) | 6.4k | 不确定 | 仿微信界面、按类型/日期/成员检索 | README 已清空；以 3.x 为主 |
| [mofan6/Moect](https://github.com/mofan6/Moect) | 735 | ✅ | 4.x 媒体分类、HTML/JSON/CSV/MD 导出 | GPL-3.0；GUI 依赖较多 |
| [LifeArchiveProject/WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis) | 4.3k | ✅ | 按类型/时间导出、schema 适配 | 依赖 wx_key；无明确 license |
| [zhuzhangxue/wechat-chat-export](https://github.com/zhuzhangxue/wechat-chat-export) | 153 | ✅ 4.1.12+ | 媒体落盘、文件原名、敏感缓存清理 | 依赖 wechatauto-replica；Apache-2.0 |
| [tzwkb/wechat-decrypt](https://github.com/tzwkb/wechat-decrypt) | 99 | ✅ Win/macOS | 只读、Agent Skill、媒体清单、MCP | MIT；与本 skill 形态最接近 |
| [93857536-pixel/WeChatExporter](https://github.com/93857536-pixel/WeChatExporter) | 82 | ✅ | 单文件 HTML、媒体内嵌、OCR/语音转写 | 默认带水印；.NET GUI |
| [0xlane/wechat-dump-rs](https://github.com/0xlane/wechat-dump-rs) | 曾 1k+ | ✅ | v4 key 内存分析、YARA 思路、raw key 校验 | 仓库可能已不可用；只读思路可参考 |
| [wechatauto-replica](https://pypi.org/project/wechatauto-replica/) | PyPI | ✅ | 图片 V2 结构、cfgDword 派生、只读 key 扫描 | 第三方包；不要直接执行，读源码/文档即可 |
| [BlueMatthew/WechatExporter](https://github.com/BlueMatthew/WechatExporter) | C++ GUI | 3.x | HTML 导出、增量导出设计 | 不依赖 |

> Stars 和 4.x 支持来自 2026-11-16 的公开信息，可能已变化；使用前重新确认。
> 多个项目曾因合规压力删库/清空 README，不要把它们作为长期依赖。

## 3. 升级时怎么用这些参考

1. 先跑 `node scripts/wechat42-export.mjs doctor` 和 `status`，记录微信版本、配置、work/venv 状态；
2. 再看 vendor 上游 `wechat-exporter` 有没有同版本/相邻版本的提交；
3. 重点对比：
   - key 配置对象/字符串/掩码/偏移；
   - SQLCipher 页大小、KDF、HMAC 参数；
   - DB 表名/列名/分片；
   - 消息复合 `local_type`、appmsg 子类型；
   - 图片 `.dat` 格式（V2/V3/WXGF）、视频/文件路径；
   - 表情 seed/缓存布局；
4. 只把它们的实现当“假设”，用本 skill 的 HMAC 校验、`native-backend.py check`、小范围 canary 和 `audit --deep` 验证；
5. 不要直接运行第三方 DLL/EXE；需要 WCDB 时使用官方仓库或自行编译。

## 4. 与本 skill 的边界

- 4.2 默认只依赖 Node + Python + 通用库；`weflow-cli` 只用于 `--legacy-weflow`。
- vendor 目录是唯一复制的第三方代码；升级 vendor 时保留 `LICENSE` / `NOTICE.md`。
- 4.1 代码保留但暂不维护；旧实现见 tag `archive-main-python-4.1-2026-11-18`。
- 参考项目均**不在**调用链里；如果未来某个项目提供了新算法，优先移植**算法**到 `scripts/lib/`，而不是引入可执行文件或 hook 组件。
