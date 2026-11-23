# 需求与待办记录

> 本文件记录“已完成 / 已记录但暂不实现 / 明确不包含”，避免后续把范围混在一起。  
> 当前功能、已知限制与按优先级排列的待办见 `STATUS.md` / `TODO.md`。

## 一、当前已完成（当前 skill 范围）

| 编号 | 需求 | 状态 | 说明 |
| --- | --- | --- | --- |
| REQ-001 | 导出文字消息 | ✅ | `messages.jsonl` / `transcript.md` / HTML |
| REQ-002 | 导出传输文件 | ✅ | `media/files/`，缺失写原因 |
| REQ-003 | 导出图片 | ✅ | `media/images/`，含封面/表情 |
| REQ-004 | 导出视频 | ✅ | `media/videos/`，含封面；只导本机缓存过的 |
| REQ-005 | 按时间筛选 | ✅ | `--from/--to/--date/--days`，本地时区 |
| REQ-006 | 按群筛选 | ✅ | `--group/--all-groups` |
| REQ-007 | 按个人筛选 | ✅ | `--person/--all-persons` |
| REQ-008 | 可浏览产物 | ✅ | 批量 `index.html` + 单会话筛选页 |
| REQ-009 | 机器可读 | ✅ | `messages.jsonl` / `messages.json` / manifest |
| REQ-010 | 缺失可解释 | ✅ | `missing.reason`，不猜路径 |
| REQ-011 | 只读、本地、不上传 | ✅ | 源库只读；无网络调用 |
| REQ-012 | 批量/dry-run/部分失败 | ✅ | `--all-*`、`--dry-run`、`--allow-partial` |
| REQ-013 | 微信 4.2 后端 | ✅ | `wechat42-export.mjs` + `wechat-exporter` + sandbox |
| REQ-016a | 语音音频导出（4.2 自动 / 4.1 wrapper） | ✅ | SILK→WAV（`pysilk`），`media/voice/` + `<audio>`；4.1 用 `wechat41-export.mjs` |
| REQ-015 | 文章链接/公众号 | ✅ 链接 fallback + `--all-official` | 正文抓取仍待定 |
| REQ-018 | doctor / compat / check-updates | ✅ | doctor + compat.json + check-updates |
| REQ-017 | Level 1 vendor / Level 2 脱离 | ✅ Level 1；✅ Level 2（4.1/4.2） | 默认 `native-backend.py`；`--legacy-weflow` 回退 |
| REQ-019 | V2 加密视频解码 | ✅ | `lib/v2_decode.py`、`v2-decode.py`、4.2 media map 自动尝试 |
| REQ-020 | 表情 | ✅ 内置表情码 + 自定义表情包 | 见下文 |
| REQ-014 | 收藏导出 | ⏸ 已记录，暂不实现 | 见下文 |
| REQ-016b | 语音转写 | ⏸ 已记录，暂不实现 | 见下文 |
| REQ-021 | 只读硬约束 | ✅ | 命令白名单 + 输出路径保护 |
| REQ-022 | 完整性校验 | ✅ | `wechat-audit.py`；数据库/原始/导出三层对账；`audit.json` |
| REQ-023 | 撤回记录 | ✅ | 批量撤回匹配原消息；单条撤回/孤立记录；`revokes.json` + `revoked` 标记 |

### 验证基线（微信 4.2.0.13）

- 2026-10-26：61 条，文字 39、图片 18（找到 13）、文件 4（找到 4）；
- 2026-11-03：33 条，文字 25、图片 6（找到 6）、文件 1（找到 1）、视频 1（找到 1，含封面）；
- 2026-11-13（SEx）：47 条，文字 43、语音 4（找到 4，WAV）；
- 2026-11-02（某群）：21 条表情消息，18 条解密成功（PNG 3 / GIF 15），3 条本机无缓存；
- `npm test`：59 项通过；`npm run test:py`：46 项通过。

## 二、已记录、暂不实现

### REQ-014 收藏导出

**现状**

- 收藏是独立的 `favorite/favorite.db`，不是聊天会话。
- `weflow-cli fav list/export` 已支持元数据：
  - `fav list --type text|image|video|article|chatrecord --keyword ... --json`
  - `fav export markdown|json`
- 不支持时间范围；不导出媒体本体。
- 本 skill 尚未接入 `fav` 子命令。
- 4.2 sandbox 已重加密 `favorite.db`，但 `favDbPath/favKey` 还没配置。

**需要什么**

- DB：`favorite/favorite.db` + 收藏密钥（`favKey` 64hex，或 `favPassphrase` 派生）；
- 时间：`update_time`/`create_time`，客户端过滤；
- 类型：article / image / video / file / chatrecord / text；
- 媒体：收藏里的图片/视频/文件本体；
- Skill：`favorites` 子命令 + `favorites.jsonl` / `favorites.md` / HTML / media 包；
- 文章正文：可选，需要联网抓取。

**决策**：待定，先不做。

### REQ-015 文章链接 / 公众号

- 已完成：链接消息 default `--types` 含 `link`；`message.appUrl`/`appTitle`/`appDescription` fallback；`--all-official` 批量导出。
- 仍待做：
  - 文章正文不抓取；
  - 公众号/链接真实 canary 待补。

### REQ-016b 语音转写

- 4.2 语音音频已导出（`media/voice/`，WAV）；4.1 用 `wechat41-export.mjs` 自动生成映射后导出。
- 转写未做。
- 后续可选：ASR；需要额外依赖和隐私评估。

### REQ-017 Level 1 / Level 2

- Level 1：已完成。`keys.py/decrypt.py/message_parser.py/export.py/media.py/proc.py` vendor 到 `scripts/vendor/wechat42/`，默认不再依赖外部 `--tool-dir` / `wechat-exporter` clone。
  - **边界**：Level 1 不包含 `weflow-cli`。
- Level 2：**4.1/4.2 已完成（2026-11-18）**。`scripts/native-backend.py` + `scripts/native-weflow.mjs` 直接读取 `work/`，实现 `check/sessions/contacts/export json --media`；4.1 由 `wechat41-decrypt.py` 先解密到 `work/`。默认不再调用 `weflow-cli`，`--legacy-weflow` 可回退对照。
  - 4.2 实测：文字 9/9、图片 2/2、文件 3/3、语音 4/4、视频 1/1；audit 通过。
- Level 2（4.1）：已完成原生路径。`wechat41-decrypt.py` 用 SQLCipher 把加密库解到独立 `work/`，再由 `native-backend.py` 读取；`wechat41-export.mjs` 默认走该路径，`--legacy-weflow` 回退。
  - 合成 SQLCipher 测试通过（46 项 Python 测试含 4.1 用例）；本机无 4.1 真机数据，真机 canary 待补。
- 需要 golden/canary 对比，不能零回归承诺。

### REQ-019 V2 加密视频

- 已完成：`lib/v2_decode.py`（AES-128-ECB + XOR，kvcomm code + wxid）、`v2-decode.py`、4.2 media map 自动尝试。
- 当前本机 1331 个视频均为普通 MP4，0 个 V2；合成测试已覆盖。

### REQ-018 doctor / compat / check-updates

- 已完成：`doctor`（配置/沙箱/venv/weflow 读库/sessions）、`compat.json`、`check-updates`（对比 GitHub HEAD，只报告不自动更新）。
- 待做：把四类媒体冒烟纳入 doctor。

### REQ-020 表情

- 已完成：
  - 内置表情码 → emoji（`lib/faces.mjs`），只在 HTML/Markdown 渲染，JSONL 保留原文；
  - 自定义表情包（`local_type 47`）解密导出（`lib/sticker.py`）：AES-128-CBC 解密 + `wxgf`/H.265 → PNG/GIF；
  - 种子来源：配置 `emoticonSeed` 或微信进程内存扫描（`wechat-sticker-seed.py`）；
  - 4.1/4.2 wrapper 自动生成表情映射并并入 `--media-map`。
- 局限：
  - 没在微信里打开过的表情包本机没有缓存，无法恢复（记 `sticker-not-local`）；
  - 内存扫描需要微信正在运行；否则要事先配置 seed；
  - `wxgf` 转码需要 ffmpeg（`imageio-ffmpeg`）；
  - 微信自带的原生表情美术是私有的，这里用最接近的标准 emoji 近似。

## 三、只读硬约束（REQ-021）

- 已完成：
  - `runWeFlow` 命令白名单：只允许 `check/sessions/contacts/media/export json|md`；
  - 输出路径保护：Node 侧拒绝微信目录内的 `--output`/`baseDir`；Python 侧 `lib/safety.py` 拒绝账号目录内的 `work/sandbox/home`、表情/语音/媒体映射输出；
  - `doctor` 输出 `readOnly` 审计字段。
- 不做：任何发送、转发、删除、编辑、撤回、修改数据库的接口。

### REQ-022 完整性校验

- 已完成：`wechat-audit.py` 独立查库对账；4.2 导出后自动跑，4.1 指定 `--output` 时自动跑；`audit` 子命令与 `--no-audit`；报告 `audit.json`，不完整退出码 5。
- 检查：数据库行数 = 原始 JSON = 导出 + 按类型跳过；重复 id、时间窗外、`coverage.mayHaveMore`、媒体引用是否存在。
- 局限：只能证明"库里有的都导出了"；本机未缓存的媒体仍记为缺失。

### REQ-023 撤回记录

- 已完成：`wechat-revoke-map.py` 读取 `general.db` 的 `revokebatchmessage` / `revokemessage`；批量按 `msg_local_id+msg_create_time` 匹配原消息，单条按 `server_id` 匹配；`revokes.json`、`revoked` 标记、HTML/Markdown 标记；4.1/4.2 wrapper 自动生成（`--no-revoke-map` 关闭）。
- 本机实测：2 条批量撤回 2/2 匹配；970 条普通单条撤回只有提示，`revokemessage` 0 行。
- 局限：普通单条撤回的原内容通常已被删除，不能恢复；批量撤回依赖原消息行仍在。

## 四、明确不包含

- **GUI / Web 管理后台 / 可视化控制台 / 安装向导等工程化界面**（本仓库定位是 Claude Code Skill；导出 HTML 只是浏览产物）；
- **写入、转发、新增、删除、编辑、撤回聊天内容**（见 REQ-021）；
- 朋友圈（`sns.db`）导出；
- 微信收藏（在 REQ-014 决策前不做）；
- 手机端 / 他人设备 / 远程静默运行；
- 绕过微信服务条款或非本人授权的数据。

## 五、更新参考

后续微信版本升级前先读：

- `SKILL.md` → “后续微信版本升级参考”
- `DESIGN.md` → “13. 后续微信版本升级参考库与排查手册”
