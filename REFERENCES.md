# 参考项目清单（仅参考，不作为运行时依赖）

记录日期：2026-11-16（微信 4.1.13.7 基线）
用途：微信升级到 4.3/4.4 或更高版本时，快速回看这些项目有没有新的 key 定位、
SQLCipher 参数、DB schema、图片/视频格式的处理思路。

> 重要：本 skill **不 import、不调用、不打包**下列任何项目。
> 它们只作为逆向/实现参考；使用前先确认仓库是否仍可用、许可证、以及是否与当前
> 微信版本匹配。不要直接运行来源不明的二进制或 DLL。

## 1. 明确不依赖的清单

| 项目/工具 | 类型 | 当前 skill 的处理 |
|---|---|---|
| [BlueMatthew/WechatExporter](https://github.com/BlueMatthew/WechatExporter) | C++ GUI，iTunes 备份/3.x | 不依赖；只参考 HTML 导出和增量导出设计 |
| [zhuobichen/weflow-cli](https://github.com/zhuobichen/weflow-cli) | CLI，4.x，MCP | 不依赖；只参考 Markdown 导出、图片落盘、文件原样拷贝 |
| [hicccc77/WeFlow](https://github.com/hicccc77/WeFlow) | Electron GUI，4.x | 不依赖；本机旧账号 key 的 WeFlow 配置是**可选**来源，不是运行依赖 |
| `wechat-export/` 旧目录 | 本项目早期引擎 | 不 import；skill 已把引擎复制到 `lib/`，旧目录仅作开发参考 |
| Node / `imgdec.js` / WeFlow 原生 addon | 图片解密辅助 | 已移除；图片解密改为纯 Python |
| 截图 / OCR / UI 自动化 | 截图方案 | 完全不用；只读数据库 + 本地媒体 |
| 云端 API / 在线服务 | 网络 | 不用；全程本地 |

## 2. 调研过的 GitHub 项目

| 项目 | 约 Stars（2026-11-16） | 4.x | 可参考的点 | 注意事项 |
|---|---:|---|---|---|
| [hicccc77/WeFlow](https://github.com/hicccc77/WeFlow) | 16.1k | ✅ | 图片/视频解密、导出格式、HTTP API | Electron/GUI；依赖其打包组件，不能直接当库 |
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
| [Tencent/wcdb](https://github.com/Tencent/wcdb) | 官方 | ✅ | SQLCipher/WCDB 语义、跨平台打开加密库 | **允许作为可选后端/兼容性校验**；无官方 Python binding |

> Stars 和 4.x 支持来自 2026-11-16 的公开信息，可能已变化；使用前重新确认。
> 多个项目曾因合规压力删库/清空 README，不要把它们作为长期依赖。

## 3. 升级时怎么用这些参考

1. 先跑本 skill 的 `diagnose`，记录版本、`Weixin.dll` SHA-256、schema 指纹；
2. 再去上述项目看有没有同版本/相邻版本的 issue、release、讨论；
3. 重点对比：
   - key 配置对象/字符串/掩码/偏移；
   - SQLCipher 页大小、KDF、HMAC 参数；
   - DB 表名/列名/分片；
   - 图片 `.dat` 格式（V2/V3/WXGF）、视频/文件路径；
4. 只把它们的实现当“假设”，用本 skill 的 HMAC 校验/实际导出结果验证；
5. 不要直接运行第三方 DLL/EXE；需要 WCDB 时使用官方仓库或自行编译。

## 4. 可选通用库

| 库 | 用途 | 备注 |
|---|---|---|
| [pilk](https://pypi.org/project/pilk/) | SILK 语音 → WAV | GPL-3.0；本地解码；未安装时只导出 `.silk` |
| [Tencent/wcdb](https://github.com/Tencent/wcdb) | SQLCipher/WCDB 兼容性校验 | 官方库；可选 C++ 后端 |
| `sqlcipher3` | 直接打开带 `-wal/-shm` 的库 | 可选；缺失时退回纯 Python 主库解密 |

## 5. 与本 skill 的边界

- 本 skill 的运行时依赖只有：`cryptography`、`zstandard`，可选 `sqlcipher3`，
  可选 Tencent WCDB；详见 `requirements.txt`。
- 上述项目均**不在** `requirements.txt`、`lib/` 或调用链里。
- 如果未来某个项目提供了新的算法/格式，优先把**算法**移植进 `lib/`，
  而不是引入它的可执行文件或 hook 组件。
