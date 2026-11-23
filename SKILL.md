---
name: wechat-export
description: 导出本机微信桌面客户端的聊天记录（文字、图片、传输文件、视频、语音、表情、撤回、引用、合并转发），支持按时间范围、按群、按个人筛选，生成可浏览的 HTML、Markdown、JSONL 和媒体文件包，并可做完整性校验。当用户说“导出微信聊天记录”“把某个群/某个人某段时间的聊天导出来”“备份微信群里的图片/文件/视频”“按时间整理微信记录”时使用。仅适用于用户本人设备上已登录的微信桌面客户端，不适用于手机端或他人设备。
---

# 微信聊天记录导出（按群 / 按人 / 按时间）

把本机微信桌面客户端的聊天记录导出成一个**自包含目录**：文字、图片、文件本体、视频都在里面，附带可筛选的 HTML 浏览页和机器可读的 JSONL。

数据读取与解密：4.1/4.2 默认都由原生后端完成（4.1 `wechat41-decrypt.py`，4.2 `native-backend.py`）；`--legacy-weflow` 可回退到 [`weflow-cli`](https://github.com/zhuobichen/weflow-cli)。全程本机只读，不上传。

- 目标解析：按群名 / 人名 / 会话 ID 选一个或多个会话
- 时间编排：`--from/--to`、`--date`、`--days`
- 范围媒体抽取：`weflow-cli` 的 markdown 导出只支持单日，本 skill 会按“有图片/文件的日期”逐日抽取
- 视频落盘：`weflow-cli` 原生只给 `[视频]` 标签，本 skill 从 `msg/video` 按 md5 找到 mp4 并复制
- 统一产物：`index.html` / `transcript.md` / `messages.jsonl` / `manifest.json` / `media/`
- 内容还原：引用消息（`quote`，HTML 引用块）、合并转发/笔记（`records`，HTML 展开列表）
- 特殊消息：转账金额、红包、位置经纬度、名片账号进入 JSONL；HTML/Markdown 结构化展示
- 4.2 离线 key 缓存：`setup` 成功后保存 `<baseDir>/keys.json`；后续 `setup` 优先用缓存，不需要微信重新登录/运行。缓存失效会自动回退到进程提取；`clean` 默认保留，`--purge-keys` 删除。

## 适用与边界

**适用**

- Windows 10/11 上的微信 4.2.x 桌面客户端（当前维护路径）
- 微信 4.1 / 3.x 代码保留或尽力兼容，但**不在当前维护/验收范围**
- 用户本人已登录、且有权访问的本地数据

> **版本提示**：`weflow-cli` 当前验证到微信 4.1.15.56。微信 **4.2** 不走 `weflow-cli init`：由 vendor 提取每库 raw key、解密到 `work/`，默认用 `native-backend.py` 直接导出；`--legacy-weflow` 才构造 sandbox 给 `weflow-cli` 读。`--check` 会输出 `wechatCompatibility.verdict`。
>
> **当前维护范围**：微信 4.2.x。4.1 代码保留但暂不维护/未真机验证；旧实现见 tag `archive-main-python-4.1-2026-11-18`。安全边界见 [`SECURITY.md`](SECURITY.md)，升级排查见 [`VERSION_MAINTENANCE.md`](VERSION_MAINTENANCE.md)，参考项目见 [`REFERENCES.md`](REFERENCES.md)。

**不适用**

- 手机微信、iCloud/Android 备份
- 他人设备、远程静默运行、监控/跟踪
- 未登录的微信账号、或已退出后没有本地缓存的记录

> 导出的内容包含真实聊天和第三方个人信息。默认只在本机处理；不要上传、不要提交 Git、不要发公开链接。

## 只读硬约束（不写入 / 不转发 / 不增删改）

本 skill 的契约是：**只读取、解析、导出副本**。不写入微信聊天内容，不转发，不新增、删除、编辑、撤回任何消息。

这不是文档承诺，运行时有三层强制：

1. **weflow-cli 命令白名单**：`runWeFlow` 在启动子进程前检查参数，只允许
   `check` / `sessions` / `contacts` / `media` / `export json|md`（以及 `--version`/`--help`）。
   `send`、`forward`、`delete`、`edit`、`revoke`、`config`、`fav`、`assistant` 等一律拒绝，退出码 4，`code: WEFLOW_READ_ONLY_VIOLATION`。
2. **输出路径保护**：导出目录、4.2 的 `baseDir/sandbox/work/home`、表情/语音/媒体映射输出，都不能位于微信账号目录内。
   Node 端（`resolveOutputDir`、`wechat42-export.mjs`）与 Python 端（`lib/safety.py` 的 `assert_outside`）双侧检查；违反时直接失败，不降级为警告。
3. **读取与复制语义**：
   - 4.2 只读取 `Config.Cipher` 与源库字节；解密到账号目录外的 `work/`，再重加密到 `sandbox/`。
   - 图片/文件在导出包内是硬链接，但链接的是 `raw/`（导出器写出的副本，不是微信源文件）。
   - 视频、表情包是**复制**，不是硬链接，避免编辑导出文件反向影响源。
   - 全程无上传；`check-updates` 只做 `git ls-remote`，不下载、不执行上游代码。

**仍然会写的地方**（都在微信数据之外）：

| 位置 | 用途 |
| --- | --- |
| `--output` 导出目录 | 唯一长期保留的产物 |
| `~/.wechat-export/wechat42/{work,sandbox,home}` | 4.2 临时解密/重加密/配置；`clean` 可删 |
| `~/.wechat-export/wechat41/` | 4.1 临时语音/表情映射 |
| `~/.wechat-export/wechat42.json` | 4.2 配置 |

微信客户端自己会写自己的库，这是微信的行为，与本 skill 无关。需要审计时可跑 `node scripts/wechat42-export.mjs doctor`，输出里的 `readOnly.enforced` 会列出允许的命令、禁止的操作和写入位置。

## 微信 4.2 后端（内置 vendor）

微信 4.2 的密钥不再是一把可由 `weflow-cli init --passphrase` 使用的共享口令，而是每库独立 raw key。`weflow-cli` 的自动 Hook 对 4.2 不生效，因此需要单独的 4.2 后端：

- 核心已 vendor：`scripts/vendor/wechat42/`（来自 `TangGod991021/wechat-exporter`，MIT，commit `cc718ef`）
  - 这里的“vendor”只覆盖**密钥提取/解密/消息解析**。
  - 4.2 默认使用 `scripts/native-backend.py` + `scripts/native-weflow.mjs` 读取 `work/` 并导出，不再调用 `weflow-cli`；`--legacy-weflow` 可回退。
  - 4.1 默认也走原生路径（`wechat41-decrypt.py` → `work/` → native backend）；`--legacy-weflow` 才走 `weflow-cli`。
- 本 skill 的 `scripts/wechat42-export.mjs` 负责：
  1. 调用 vendor 提取每库 raw key（也可用 `--tool-dir` 指向外部上游做更新验证）；
  2. 解密到临时工作目录；
  3. 用**同一个 raw key + salt** 重新加密到 sandbox（仅 `--legacy-weflow` 需要，让 `weflow-cli` 能读所有分片）；
  4. 把 sandbox 的 `msg/cache/resource` 用 junction 指回真实账号（媒体只读）；
  5. 生成视频/图片/语音媒体映射（补导出 JSON 里缺失的 video md5；语音 SILK→WAV；V2 视频自动解码）；
  6. 调用标准 `wechat-export.mjs`，默认由原生后端读取 `work/`，产出同样的 HTML/Markdown/JSONL/media 包；`--legacy-weflow` 时仍由 `weflow-cli` 读取 sandbox。

**前置：** 用户必须明确授权使用该 MIT 上游来读取本机微信数据。默认使用内置 vendor，不需要 clone `wechat-exporter`；更新/对照上游时可 `--tool-dir <外部目录>`。**4.1/4.2 默认都已不依赖 `weflow-cli`；只有 `--legacy-weflow` 使用。**

```bash
node scripts/wechat42-export.mjs setup \
  --account-root "C:\Users\<你>\...\xwechat_files\<账号目录>"
```

之后和普通导出一样：

```bash
# 单个群最近 7 天（自动生成媒体映射）
node scripts/wechat42-export.mjs --group "项目群" --days 7

# 单日文字+图片+文件+视频+语音
node scripts/wechat42-export.mjs --target "50055365721@chatroom" --date 2026-11-03 \
  --types text,image,file,video,voice,link,history

# 关键词搜索（4.2）
node scripts/wechat42-export.mjs search --query 1684X --from 2026-08-17 --to 2026-11-17

# 查看/清理/体检/更新检查
node scripts/wechat42-export.mjs status
node scripts/wechat42-export.mjs doctor
node scripts/wechat42-export.mjs check-updates
node scripts/wechat42-export.mjs clean   # 删除 sandbox/明文 work/home/_stickers，保留导出结果和 keys.json
node scripts/wechat42-export.mjs clean --purge-keys  # 同时删除离线 key 缓存
node scripts/wechat41-export.mjs clean   # 删除 wechat41/work/_stickers/映射临时文件
```

公众号批量：

```bash
node scripts/wechat42-export.mjs --all-official --days 7 --types all
```

**验证结果（本机实测）**

- `2026-10-26`：61 条，文字 39、图片 18（找到 13）、文件 4（找到 4）；
- `2026-11-03`：33 条，文字 25、图片 6（找到 6）、文件 1（找到 1）、视频 1（找到 1，含封面）；
- `2026-11-13`：SEx 群，47 条，文字 43、语音 4（找到 4，WAV + `<audio>`）。

**语音依赖：** 4.2 语音用 `silk-python`（`pysilk`，Windows 预编译 wheel）把 SILK v3 解成 24kHz WAV；安装：

```bash
python -m pip install silk-python
```

`pilk` 在 Windows 上需要 MSVC 编译，不作为首选。语音数据在 `media_*.db` 的 `VoiceInfo` 表，**不一定在 media_0.db**。

**4.1 加密库：** 用 `wechat41-export.mjs`，默认走原生路径（`wechat41-decrypt.py` 解密到独立 `work/`，再走 native backend；语音/表情/视频/文件由原生 `--media` 处理）：

```bash
node scripts/wechat41-export.mjs --target "<群ID/wxid>" --date 2026-11-13 --types all
```

原生路径需要 `sqlcipher3`（`python -m pip install sqlcipher3-wheels`），配置文件默认 `~/.weflow-cli/config.json`，可用 `--config` 指定。  
`--legacy-weflow` 可回退到旧的 weflow-cli 语音映射路径。手动方式（legacy 调试/自定义）：

```bash
python scripts/wechat41-voice-map.py \
  --weflow-cli-dir "<weflow-cli>/scripts" \
  --config "C:\Users\<你>\.weflow-cli\config.json" \
  --target "<群ID/wxid>" --from 2026-11-13 --to 2026-11-13 \
  --out voice-map.json

node scripts/wechat41-export.mjs --target "<群ID/wxid>" --date 2026-11-13 \
  --types text,voice --media-map voice-map.json
```

**表情（4.1/4.2 都支持）**

微信里的"表情"有两类，导出时都会处理：

- **内置表情码**（正文里的 `[玫瑰]`、`[捂脸]`）：HTML/Markdown 渲染成 emoji，JSONL 保留原始文字。
- **自定义表情包**（独立的"表情"消息，`localType 47`）：自动解密并落盘到 `media/stickers/`（PNG/GIF）。

表情包是 AES-128-CBC 加密的，key 由账号 seed 派生（seed 只在微信进程内存里）。导出时自动探测 seed：先看配置里的 `emoticonSeed`，否则扫微信进程内存（**要求微信正在运行**），命中后用样本解密校验。

```bash
# 手动拿 seed（微信需在运行）
python scripts/wechat-sticker-seed.py --account-root "<xwechat_files>/<账号目录>"

# 指定 seed；或不导出表情包
node scripts/wechat42-export.mjs --target "<群ID>" --days 7 --sticker-seed 206269640
node scripts/wechat42-export.mjs --target "<群ID>" --days 7 --no-sticker-map
```

微信的 `wxgf` 容器（H.265 裸流）要 ffmpeg 转码：`python -m pip install imageio-ffmpeg`。
本机没缓存的表情包记为缺失（`sticker-not-local`），不猜、不伪造。

**安全与维护**

- `sandbox/`、`work/`、`home/` 都含敏感数据：`work/` 是明文库，`home/.weflow-cli/config.json` 含 sandbox 密钥。不用时执行 `clean`。
- 上游是版本敏感层：微信升级后先更新 `wechat-exporter`，再 `setup --refresh`；本 skill 的解析/导出层不用重写。
- 上游可能像 chatlog/PyWxDump 一样被下架；建议保留一份本地副本并记录 commit。

## 当前范围与暂不包含

**已包含**：文字、图片、传输文件、视频、语音音频、表情（内置表情码渲染 + 自定义表情包解密导出）、文章链接、公众号批量（`--all-official`）、V2 加密视频解码；按时间/群/个人；4.1/4.2 原生后端（`wechat41-decrypt.py` / `native-backend.py`），`--legacy-weflow` 可回退；`doctor` / `check-updates`；HTML/Markdown/JSONL/媒体包。

**暂不包含**（记录在 `BACKLOG.md`，后续再决定）：

- **写入、转发、新增、删除、编辑、撤回聊天内容**（只读硬约束，见上节）
- 微信收藏（已记录，暂不实现）
- 朋友圈
- 公众号文章正文抓取
- 4.1 真机数据验证（原生路径已通过合成 SQLCipher 测试，本机无 4.1 数据）
- 语音转写（已记录，暂不实现）
- 真实 V2 视频 / 公众号链接 canary（本机无样本，合成测试已覆盖 V2）

## 后续微信版本升级参考（先读这里，少走弯路）

> 这一节不是只针对 4.3/4.4；微信以后任何版本升级（4.x、5.x 或更高）都按这里的层和方法排查。

微信升级后不要从零重写。先按层定位，再查对应参考库：

| 层 | 优先参考 | 能解决什么 | 不能解决什么 |
| --- | --- | --- | --- |
| 密钥提取 | `TangGod991021/wechat-exporter`（MIT，4.2 基线）、`SmileGrassSky620/wechat-local-reader`、`fangtimmy/wechat4-db-decrypt`、`anpingjing/wechat-chatlog-decrypt-export`、`TANGandXUE/wcdb-key-tool` | `Config.Cipher` 布局、XOR 掩码、错误页 1 HMAC 校验、进程/模块定位 | 不能保证新版本直接可用，需要按新布局改 |
| DB 解密/读取 | `Tencent/wcdb`（BSD-3）、`sqlcipher3`/`sqlcipher3-wheels`、`pycryptodome`/`cryptography` | SQLCipher page/reserve/KDF、读库、schema | **不提供微信密钥** |
| Schema/消息解析 | `zhuobichen/weflow-cli`（MIT）、`wechat-exporter` 的 `message_parser.py`/`export.py`、WCDB/sqlite3 `PRAGMA table_info` | `Msg_*`/`Name2Id`/`local_type`/appmsg/zstd/群发送者 | 新子类型仍需按样本更新 |
| 媒体 | `weflow-cli/scripts/media_index.py`、`export_chat_html.py`、`wechat_voice.py`、`wechat_emoticon.py`、`wechat-exporter/app/core/export.py`、`media.py`、`qilaidev/wechat-localmind`（MIT） | hardlink.db 三表、msg/attach|file|video、V2 图片、HEVC、SILK 语音、表情包 AES 解密 | 未缓存的媒体无法凭空恢复 |
| 导出/HTML | 本 skill 的 `bundle.mjs`/`html.mjs`/`markdown.mjs`；`weflow-cli`/`wechat-exporter` 的导出实现 | HTML/Markdown/JSONL/媒体包 | — |

**更新顺序**：`doctor` → 密钥层 → 解密层 → schema/parser → 媒体层 → canary/全量测试 → 更新 `compat.json` 和版本号。

**不要走的弯路：**

- 只靠 WCDB 取密钥——WCDB 开源的是框架，不含微信客户端的 `Config.Cipher`/密钥。
- 以为拿到每库 raw key 就能还原共享 passphrase——PBKDF2 不可逆。
- 把已删库/停更项目（chatlog、PyWxDump、MemoTrace、wechat-dump-rs）当唯一依据。
- 假设 `Config.Cipher` 的字符串/偏移/XOR 掩码永远不变。
- 用 `weflow-cli export md --date` 抽媒体——1.21.17 会误报“不支持 from/to”；用 `export json --media --date`。
- 把 `localId` 当全局唯一——用 `localId:createTime`。
- 把高 32 位=6 的文件消息当图片——已修复，更新时保留这条。
- 硬链接微信源视频——会反向修改源文件；视频要复制。
- 语音只找 `media_0.db`——`VoiceInfo` 可能在 `media_1.db` 等分片；要扫 `media_*.db`。
- 文章链接只看 `rawContent`——4.2 部分行是空的；要用 `message.appUrl`/`appTitle` fallback。
- 用 `pilk` 解 SILK——Windows 上要 MSVC；优先 `silk-python`（`pysilk` 预编译 wheel）。
- 以为表情包在 `msg/attach` 里——它们在 `business/emoticon/{Persist,Thumb}` 和 `cache/<月>/Emoticon`，且是 AES-CBC 加密的。
- 没有备份就降级/重装微信。

> 更详细的入口函数、文件路径和排查手册见 `DESIGN.md` 的“后续微信版本升级参考库”附录。

## 前置条件

在开始前先确认：

- Node.js **22.13+**
- Python **3.10+**（原生读取/媒体都需要）
  - 如果 `python --version` 打开 Microsoft Store，说明命中的是“应用执行别名”；请安装 python.org 的 Python，并在“管理应用执行别名”里关掉 python/python3 的商店别名。
  - **一键安装 Python 依赖**：`python -m pip install -r requirements.txt`（cryptography、pycryptodome、zstandard、silk-python、imageio-ffmpeg）。
  - **4.1 保留路径 / legacy sandbox（暂缓维护）**：可选 `python -m pip install sqlcipher3-wheels`。
  - **语音导出**：`silk-python`（`pysilk` 预编译 wheel）；`pilk` 在 Windows 上需要 MSVC，不作为首选。
  - **表情包导出**：`imageio-ffmpeg`（微信 `wxgf` 容器是 H.265 裸流，要 ffmpeg 转码）；`cryptography` 用于 AES 解密。
- 微信桌面客户端已安装并登录
  - **表情包解密需要微信正在运行**（seed 在进程内存里），除非已用 `--sticker-seed` 指定或配置了 `emoticonSeed`。
- `weflow-cli` **可选**：只有 `--legacy-weflow` 回退/对照需要；4.2 默认原生后端不需要。

一次性安装：

```bash
npm install -g weflow-cli
weflow-cli setup --yes      # 创建 Python venv 并安装 sqlcipher3 等依赖
weflow-cli init             # 交互式：发现微信数据目录、提取/验证密钥
weflow-cli check            # 看到 Python、消息库都为 ✓ 再继续
```

`weflow-cli init` 需要微信正在运行/已登录，并要求交互确认。**让用户在自己的终端执行**；如果当前会话支持 `!` 前缀，可以提示用户输入：

```text
! weflow-cli init
```

> **命令入口**：下面的示例默认用 4.2 入口 `scripts/wechat42-export.mjs`；微信 4.1 把命令名换成 `scripts/wechat41-export.mjs`，参数相同。`scripts/wechat-export.mjs` 是内部主命令，只在调试/对照时直接调用。

## Agent 执行流程

### 第 0 步：把需求拆成四个字段

| 字段 | 例子 | 缺失时怎么办 |
| --- | --- | --- |
| 目标 | “项目群”“张三”“所有群” | 跑 `--list --json`，让用户确认 |
| 时间 | “最近 7 天”“2026-01-01 到 2026-01-31”“全部” | 默认全部时间，但要提醒可能很大 |
| 类型 | 文字 / 图片 / 文件 / 视频 / 全部 | 默认 `text,image,file,video,voice,emoji,link,history,quote,transfer,redpacket,location,card,call` |
| 输出位置 | 默认 `~/.wechat-export/<时间戳>` | 一般不用问；涉及交付时再指定 |

### 第 1 步：环境体检

```bash
cd <skill-dir>
node scripts/wechat42-export.mjs --check
```

需要机器可读结果时加 `--json`。不要跳过这一步直接批量导出：`NOT_INITIALIZED` / `PYTHON_DEPS_MISSING` 在这里就能给出明确动作。

> `--check` 会打印本机的微信账号根目录与视频目录路径。分享体检结果前先脱敏（去掉账号目录名）；`weflow-cli check --report` 是另一份已脱敏的诊断。

### 第 2 步：确认目标（不要猜）

```bash
node scripts/wechat42-export.mjs --list --json
```

输出包含 `index / id / name / kind / lastMessageTime`，默认不含聊天摘要。把用户说的名字映射到 `id`：

- 群：`id` 以 `@chatroom` 结尾
- 个人：`wxid_...` 或普通微信号
- 公众号/系统账号：默认不列出；需要时加 `--include-official`

**匹配不唯一时不要自己选一个。** 把候选的 `name + id` 列给用户确认，然后用 `--target "<id>"` 精确导出。

### 第 3 步：确认范围（两种情况先问）

- `--all-groups` / `--all-persons`：可能一次导出几十个会话
- 时间范围超过 31 天：媒体抽取会变慢/占空间

先给用户一个 dry-run 计划：

```bash
node scripts/wechat42-export.mjs --all-groups --days 7 --dry-run
```

### 第 4 步：执行导出

单人 / 单群：

```bash
# 某个群最近 7 天
node scripts/wechat42-export.mjs --group "项目群" --days 7

# 某个人某个月
node scripts/wechat42-export.mjs --person "张三" --from 2026-01-01 --to 2026-01-31

# 用 ID 精确指定（同名时最稳）
node scripts/wechat42-export.mjs --target "12345678@chatroom" --date 2026-01-05
```

批量：

```bash
# 最近 3 天的所有群，只要文字和视频
node scripts/wechat42-export.mjs --all-groups --days 3 --types text,video

# 最近 7 天所有个人会话，上限 20 个
node scripts/wechat42-export.mjs --all-persons --days 7 --max-targets 20

# 先看计划，再执行
node scripts/wechat42-export.mjs --all-groups --days 7 --dry-run
```

只要某几类：

```bash
--types text          # 最快，不需要 Python
--types image,file    # 需要 Python 依赖
--types video         # 只需扫描 msg/video
--types emoji         # 表情（内置表情码 + 自定义表情包）
--types history       # 合并转发 / 笔记（展开内层文字、文件名、发送者、时间）
--types transfer,redpacket,location,card   # 转账 / 红包 / 位置 / 名片
--types all           # 全部类型（含 link/history/article/emoji 等）
```

指定输出：

```bash
node scripts/wechat42-export.mjs --group "项目群" --days 7 --output "D:\\wechat-exports\\项目群-近7天"
```

需要机器可读的最终结果：加 `--json`。进度和告警走 stderr，stdout 只有最终 JSON。

### 第 5 步：交付与核对

导出完成后，读输出根目录的 `manifest.json`（或 `--json` 的 `totals`），向用户报告：

- 输出目录
- 会话数、消息数
- 图片 / 文件 / 视频 / 语音 / 表情的“已导出 / 缺失”数量
- 缺失原因（只报原因，不要贴聊天正文）
- 打开方式：`<输出>/index.html`

**完整性**：4.2 会自动跑校验并打印"数据库 N / 原始 N / 导出 N / 跳过 N"。如果退出码是 5 或终端出现"✗"，先读 `<输出>/audit.json`，按“完整性校验”一节排查，不要直接告诉用户"导出成功"。4.1 需要手动跑：

```bash
node scripts/wechat41-export.mjs audit --output "<输出目录>"
```

需要逐条 ID 对账时给 `audit` 加 `--deep`（需要已解密的 `work/`，4.1/4.2 都支持）；它会报 `missing_ids`/`extra_ids`，能抓出计数相同但一丢一重的情况。

**不要**默认把 `messages.jsonl` 或聊天正文贴进对话；用户明确要求分析时再读。

## 输出结构

```text
<输出目录>/
├── index.html                 # 批量入口，列出每个会话
├── manifest.json              # 批次清单：范围、统计、失败项、告警
├── audit.json                 # 完整性校验报告
├── README.txt                 # 给人看的说明与隐私提示
└── <名称>_<id前8位>/
    ├── index.html             # 单会话浏览页：按类型/发送者/关键词筛选
    ├── transcript.md          # 按日期分组的 Markdown
    ├── messages.jsonl         # 一行一条消息，适合脚本/模型
    ├── messages.json
    ├── manifest.json          # 单会话统计、缺失媒体原因
    ├── revokes.json           # 撤回记录（匹配项 + 孤立项）
    ├── README.txt
    ├── raw/                   # weflow-cli 原始产物（默认保留，可 --prune-raw）
    └── media/
        ├── images/            # 图片与视频封面
        ├── files/             # 聊天里传输的文档（保留原文件名）
        ├── videos/            # 视频本体（mp4 等）
        ├── voice/             # 语音 WAV
        ├── stickers/          # 自定义表情包（PNG/GIF）
        └── index.json         # 媒体清单：路径、大小、对应消息
```

`messages.jsonl` 单条记录的主要字段：

| 字段 | 说明 |
| --- | --- |
| `id` | `localId:createTime`，会话内可用于定位 |
| `timestamp` / `iso` / `localTime` / `day` | 时间（本地时区） |
| `sender` / `senderId` / `isMe` | 发送者显示名 / 账号 ID / 是否本人 |
| `type` / `typeLabel` | text / image / file / video / voice / emoji / … |
| `text` | 可读文本或 `[图片]` / `[文件] xxx` / `[视频 12″]` / `[表情]` |
| `media` | `image` / `file` / `video` / `poster` / `voice` / `sticker` 的相对路径 |
| `fileName` / `fileSize` | 文件消息的原名与大小 |
| `stickerMd5` | 表情包消息对应的表情 id |
| `revoked` / `revoke` | 是否已撤回 / 撤回元数据（`kind`/`batchId`/`msgUniqueId`/`revokeTime`/`serverId`） |
| `missing` | 未导出的媒体及原因 |
| `rawContent` | 仅在 `--include-raw` 时出现 |

## 完整性校验

导出后要回答"有没有漏消息"，不能只看 `messages.jsonl` 的条数。4.2 自动在导出后跑校验（`work/` 存在时）；4.1 在传了 `--output` 时自动跑。校验链：

```text
数据库该时间窗内的消息行数
  == weflow-cli 返回的原始 JSON 条数
  == messages.jsonl 条数 + 按类型跳过条数
```

同时检查重复 `id`、时间窗外的记录、`coverage.mayHaveMore`、以及 JSONL 引用的媒体是否真的在磁盘上。报告写到 `<输出目录>/audit.json`；不完整时退出码 **5**，但产物保留。`--no-audit` 可跳过。

手动校验：

```bash
node scripts/wechat42-export.mjs audit --output "<导出目录>"
node scripts/wechat41-export.mjs audit --output "<导出目录>"
```

深度对账（计数一致但可能一丢一重时用）：

```bash
node scripts/wechat42-export.mjs audit --output "<导出目录>" --deep
node scripts/wechat41-export.mjs audit --output "<导出目录>" --deep
```

`--deep` 重新读取 `work/` 源库，按 `(local_id, server_id)` 对比源库 / 原始 JSON / `messages.jsonl`，报告 `missing_ids`、`extra_ids`、`exported_extra`、原始未导出条数和类型不符。它需要原生 `work/`；legacy weflow 模式不支持。边界优先用原始 JSON 的 `coverage.boundary`，没有时退回兜底值并在报告标 `reliable: false`，此时尾部消息可能漏检。

看到"不完整"时按报告里的字段排查：

| 现象 | 含义 |
| --- | --- |
| `dbMessages > rawMessages` | weflow-cli 少返回了消息；检查 `coverage`、时间参数、weflow 版本 |
| `dbMessages < rawMessages` | 导出范围与库统计不一致；检查时区/`--to` 语义、是否读错会话 |
| `导出+跳过 ≠ 原始` | 消息在类型过滤/打包阶段丢了；检查 `--types` 和 `skippedByType` |
| `deep.missingIds` 非空 | 源库有、原始 JSON 没有：读库/时间窗/分页丢行 |
| `deep.extraIds` 非空 | 原始 JSON 有、源库没有：会话或库在导出后发生变化 |
| `deep.exportedNotInRaw` 非空 | 打包阶段多出了源里没有的消息 |
| `deep.typeMismatch` 非空 | 导出的 `type` 不在 `--types` 里，类型过滤写错 |
| `duplicateIds` | `localId:createTime` 重复；检查是否同一会话被多次追加 |
| `outsideWindow` | 时间窗过滤不一致；检查本地时区 |
| `brokenMedia` | JSONL 引用了不存在的文件；检查 `media/` 是否被移动/清理 |

媒体"缺失"（`missing`）不等于消息缺失：本机从未缓存或已被微信清理的图片/文件/视频/表情无法恢复，校验只保证"数据库里有、导出里也有"。

## 撤回记录

微信的撤回分两类，导出时自动读取 `general.db` 的 `revokemessage` / `revokebatchmessage`（`--no-revoke-map` 关闭）：

| 类型 | 能否拿到原内容 | 依据 |
| --- | --- | --- |
| 批量撤回 | 能 | `revokebatchmessage.msg_local_id/msg_create_time` 指向原消息行，原行通常还在 |
| 单条撤回 | 通常不能 | 只剩 `sysmsg type="revokemsg"` 提示，无 `newmsgid`；原行已删除 |
| 单条撤回（`revokemessage` 有记录） | 可能 | 该表 `content` 可能带原文；本机 0 行，无法验证 |

产物：

- `messages.jsonl` 的对上记录带 `revoked: true` 和 `revoke` 对象（`kind`/`batchId`/`msgUniqueId`/`revokeTime`/`serverId`）；
- HTML 显示“已撤回”徽标，Markdown 标注“（已撤回）”；
- 每个会话目录有 `revokes.json`：`items`（能对上本地消息）和 `orphans`（只有撤回表记录、没有本地消息；`content` 可能带原文）。

向用户解释时区分清楚：**撤回提示**能导出；**普通单条撤回的原文**一般不在本机库里，不能承诺恢复；批量撤回和撤回前的导出快照可以。4.1 的撤回读取代码路径已实现，但本机无 4.1 数据，尚未真机验证。

## 媒体策略（重要）

`weflow-cli export md` 只支持 `--date`（单日），不支持 `--from/--to`。所以本 skill 用 `--media-strategy` 控制：

| 策略 | 行为 | 适用 |
| --- | --- | --- |
| `auto`（默认） | 有图片/文件的日期 ≤31 天：逐日抽取；更多：一次全量抽取 | 大多数情况 |
| `day` | 按有媒体的日期逐日抽取 | 范围宽但媒体分散，愿意多跑几次 |
| `full` | 一次抽取该会话全部历史媒体 | 要完整备份整个会话 |
| `none` | 不抽取图片/文件；文字和视频仍可导出 | 只要文字、或 Python 不可用 |

视频不依赖 Python：本 skill 直接扫描微信账号目录下的 `msg/video/**/<md5>.mp4`，用消息 XML 里的 `md5/rawmd5` 匹配，再复制到 `media/videos/`。

图片/文件在 `raw/` 与 `media/` 之间优先硬链接（`raw/` 本身已是 weflow-cli 写出的副本）；视频从微信 `msg/video` 复制，避免编辑导出文件时改到源文件。视频多时更慢、更占空间。

## 失败与降级

| 现象 / JSON code | 处理 |
| --- | --- |
| `WEFLOW_NOT_FOUND` | `npm install -g weflow-cli`；或用 `--weflow <cli.cjs>` 指定 |
| `NOT_INITIALIZED` | 让用户运行 `weflow-cli init`（需微信已登录） |
| `MESSAGE_DB_MISSING` | `weflow-cli init --path "<微信数据目录>"` 或 `--search-drives` |
| `PYTHON_DEPS_MISSING` | 先确保 `python --version` 能输出版本（不是打开商店），再 `weflow-cli setup --yes`；或 `--types text,video`；或 `--allow-partial` |
| `OUTPUT_NOT_EMPTY` | 换 `--output`，或加 `--force`（旧目录会改名为 `.bak-<时间戳>`） |
| `OUTPUT_INSIDE_WECHAT` | 输出不能放在微信数据目录内，换目录 |
| `AMBIGUOUS_TARGET` | 用 `--target "<id>"` 精确指定 |
| `TARGET_NOT_FOUND` | 先 `--list`；群可能不在最近会话里，用 ID 或 `--list-contacts` |
| 图片/文件 `missing` | 源数据不在本机（没下载过/被清理）或抽取失败；看 `missing.reason` |
| 视频 `video-not-local` | 本机 `msg/video` 没有这个视频；离线无法补 |
| 视频 `video-root-not-found` | 用 `--video-root "<账号目录>"` 指定；账号目录下应有 `msg/video` |
| 视频 `video-encrypted-v2` | 文件是微信 V2 加密格式；4.2 media map 会自动尝试解码，失败则原样落盘。也可手动跑 `python scripts/v2-decode.py --video-dir ... --out-dir ...` |
| `PARTIAL`（退出码 5） | 部分会话失败；`--json` 的 `failures` 有逐项原因 |

## 隐私与合规（必须遵守）

- 只处理用户本人拥有或明确获授权访问的数据。
- 不把导出目录上传到任何外部服务；不把聊天正文写进日志/Issue/PR。
- 输出目录默认在 `~/.wechat-export/`，含第三方个人信息；提醒用户按需删除。
- 不要把导出目录放进 Git 仓库（脚本会检测并告警，但不会替你清理）。
- 不用于监控、跟踪、读取他人微信；不部署到他人设备或远程静默运行。
- 交付给第三方前先脱敏，并确认对方有接收这些信息的合法依据。

## 设计取舍

为什么以 `weflow-cli` 为底座，而不是自己重写解密：

- 它已经处理了 Windows 微信 4.x 的 SQLCipher 密钥、分片消息库、图片/文件定位、只读打开；
- 它有稳定的 `weflow-message/v1` JSON 契约和 `--from/--to`；
- 它的 `media` 命令能按 md5 精确定位图片/文件/视频；
- 自己重写会重复维护最容易随微信升级失效的部分。

本 skill 只做它没做或不好用的那一层：按范围编排媒体抽取、视频本体落盘、目标/时间/类型筛选、统一产物和可浏览页面。更细的原理、数据流和测试见 `DESIGN.md`。

## 与其它 skill 的关系

- 本 skill 负责**导出原始记录**：文字、图片、文件、视频，以及可浏览/可机读的产物。
- 如果用户要的是**工作报告 / 事项时间线**，而不是原始记录，可配合本机已有的 `wechat-work-report` skill：先用本 skill 导出，或直接用它的同步/报告流程。

## 参考与替代方案

- `weflow-cli`：本 skill 的底座，CLI / MCP / 本地只读。
- [WeFlow](https://github.com/hicccc77/WeFlow)：Electron GUI，支持查看、年度报告、多格式导出和本地 HTTP API（含视频媒体）；适合人工浏览，不适合在 Claude Code 里稳定编排。
- `chatlog`（sjzar）：Go 实现，HTTP API + MCP，支持图片/语音等媒体；可作为另一条后端路线。
- PyWxDump / MemoTrace（留痕）：历史上常用，但截至 2026 年已出现删库/停更，不建议作为新依赖。

## 常用命令速查

```bash
cd <skill-dir>

node scripts/wechat42-export.mjs --check
node scripts/wechat42-export.mjs --list
node scripts/wechat42-export.mjs --list --json

node scripts/wechat42-export.mjs --group "项目群" --days 7
node scripts/wechat42-export.mjs --person "张三" --from 2026-01-01 --to 2026-01-31
node scripts/wechat42-export.mjs --target "123@chatroom" --date 2026-01-05
node scripts/wechat42-export.mjs --all-groups --days 3 --types text,video
node scripts/wechat42-export.mjs --all-persons --days 7 --max-targets 20

node scripts/wechat42-export.mjs --group "项目群" --days 7 --output "D:\\exports\\项目群"
node scripts/wechat42-export.mjs --group "项目群" --days 7 --json
node scripts/wechat42-export.mjs --group "项目群" --days 7 --dry-run
```
