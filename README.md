# wechat-export

把本机微信桌面客户端的聊天记录导出成一个可直接浏览、也可喂给脚本/模型的目录：**文字、图片、传输文件、视频**，支持**按时间、按群、按个人**筛选。

> 仅用于用户本人设备上、本人已登录并有权访问的微信数据。导出内容含第三方个人信息，请勿上传或公开。

当前功能、已知限制和待办见 **[STATUS.md](STATUS.md)** 与 **[TODO.md](TODO.md)**；设计细节见 `DESIGN.md`，暂不实现的需求见 `BACKLOG.md`。安全边界见 **[SECURITY.md](SECURITY.md)**，版本升级排查见 **[VERSION_MAINTENANCE.md](VERSION_MAINTENANCE.md)**，参考项目与第三方声明见 **[REFERENCES.md](REFERENCES.md)** / **[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)**。产物字段见 **[docs/OUTPUT_SCHEMA.md](docs/OUTPUT_SCHEMA.md)**。

## 它解决什么

微信客户端本身不提供“按群/按人/按时间段导出成文件”的能力。已有工具大体分两类：

- CLI / 自动化：解密、查询、导出，但不一定导出视频本体，也不方便按范围批量编排；
- GUI：浏览体验好，但难以在 Claude Code 等 Agent 里稳定调用。

本 skill 把两者之间缺的那层补上：

```text
微信本地库
  ├─ 4.1/4.2：SQLCipher 解密 → work/ → native-backend.py
  └─ --legacy-weflow：weflow-cli（旧读取/导出路径）
       └─ wechat-export skill（目标/时间/类型编排、视频落盘、统一产物）
            └─ index.html + transcript.md + messages.jsonl + media/
```

## 当前范围

**已包含**

- 文字消息、图片、传输文件、视频、文章链接
- 语音音频（4.2 自动；4.1 用 `wechat41-export.mjs`）
- 表情：内置表情码渲染成 emoji；自定义表情包解密导出为 PNG/GIF
- 撤回记录：批量撤回匹配原消息；单条撤回/孤立记录标记
- 引用消息：还原被引用消息的发送者/原文（HTML 引用块、Markdown 引用）
- 合并转发：展开内层文字/文件名/发送者/时间（HTML `<details>`、Markdown 引用块）
- 转账/红包/位置/名片：结构化金额、标题、经纬度、名片账号；HTML 卡片/地图链接、Markdown 链接
- 4.2 离线 key 缓存：`setup` 把逐库 raw key 保存到 `<baseDir>/keys.json`，后续可复用；`clean --purge-keys` 删除
- 联系人 / 群成员 / 头像：`contacts --output <目录>` 生成 `contacts.json`、`groups.json`、`avatars/`
- 跨会话按人 / 全局时间线：`person --sender "张三"`、`timeline`；`--incremental` 基于 `state.json` 追加
- 按会话增量：普通 `--target/--group/--person` 加 `--incremental`，只追加新消息；媒体按 sha256 去重，`check-incremental` 可复验
- 可选联网：`--fetch-articles`（仅 `mp.weixin.qq.com`）、`--download-stickers`（Tencent 白名单），默认关闭
- `decrypt-image`：单个图片 `.dat`/V2 文件解密
- 按时间（`--from/--to/--date/--days`）、按群、按个人
- 公众号批量导出（`--all-official`）
- 微信 4.2（`wechat42-export.mjs`，默认原生，当前维护）
- 微信 4.1（`wechat41-export.mjs`，代码保留但**暂不维护/未真机验证**；旧实现归档在 tag `archive-main-python-4.1-2026-11-18`）
  - **注意**：4.2 的“核心已 vendor”只指密钥提取/解密/消息解析不再依赖外部 `wechat-exporter` clone；4.2 默认原生后端，`--legacy-weflow` 才走 `weflow-cli`。
- V2 加密视频自动解码；V2 加密图片解密；WXGF/H.265 图片转 JPEG
- 小程序：title/描述/appid/pagepath/username/icon 等字段进入 JSONL
- 合并转发内层媒体：优先从本机缓存定位图片/视频/文件；拿不到标记 `forward-media-not-local`
- 导出安全：`--redact basic|strict` 脱敏；`--encrypt` 生成 AES-256-GCM 加密包；`--retention-days` 写保留期，`retention-clean` 清理到期目录
- HTML / Markdown / JSONL / manifest / media 包
- `doctor` / `compat.json` / `check-updates`

**暂不包含**（已记录在 `BACKLOG.md`，后续再决定）

- 微信收藏
- 朋友圈
- 公众号文章正文抓取
- 4.1 语音自动映射的真机验证
- 语音转写
- 4.1 真机数据验证（原生路径已有合成 SQLCipher 测试，本机无 4.1 数据）
- **写入、转发、新增、删除、编辑、撤回聊天内容**

## 只读边界

本 skill 只读、只解析、只导出副本。运行时强制：

- **weflow-cli 命令白名单**：只允许 `check` / `sessions` / `contacts` / `media` / `export json|md`。`send`、`forward`、`delete`、`edit`、`revoke`、`config`、`fav`、`assistant` 等在 spawn 前就被拒绝（`WEFLOW_READ_ONLY_VIOLATION`）。
- **输出路径保护**：导出目录、4.2 的 `baseDir/work/sandbox/home`、表情/语音输出都不能位于微信账号目录内；Node 与 Python 两侧都检查，违反直接失败。
- **复制语义**：图片/文件硬链接的是 `raw/` 导出副本；视频、表情包复制，不硬链接微信源文件。
- **无上传**：4.2 密钥提取只读进程内存；`check-updates` 只做 `git ls-remote`。

唯一会写的是导出目录和 `~/.wechat-export/` 下的临时工作目录。`doctor` 的 `readOnly` 字段可用来审计。

## 依赖边界（容易误解）

| 依赖 | 用途 | 当前是否必需 |
| --- | --- | --- |
| `wechat-exporter` 外部 clone | 4.2 密钥提取/解密/消息解析 | **不再需要**，核心已 vendor 到 `scripts/vendor/wechat42/` |
| `weflow-cli` | `--legacy-weflow` 回退；手动运行 `wechat-export.mjs` 时的读取层 | **可选**；4.1/4.2 wrapper 默认原生不再需要 |
| `sqlcipher3` | 4.1 原生解密（`wechat41-decrypt.py`） | 4.1 原生导出需要 |
| Node.js | CLI 编排、参数解析、HTML/Markdown 产物 | 必需 |
| Python | 媒体抽取、语音/表情、audit、4.1/4.2 解密 | 必需 |
| `silk-python` | 语音 SILK→WAV | 语音功能需要 |
| `imageio-ffmpeg` | 表情 wxgf/H.265 → PNG/GIF | 表情包转码需要 |

一句话：**Level 1 去掉了外部 `wechat-exporter` clone；Level 2 的 4.1/4.2 原生后端都已接入，`weflow-cli` 只在 `--legacy-weflow` 回退时使用。**

## 安装为 Claude Code Skill

把整个目录复制或链接到用户级 skill 目录：

```bash
# macOS / Linux
ln -s "$(pwd)" ~/.claude/skills/wechat-export

# Windows PowerShell（管理员或开发者模式）
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\wechat-export" -Target (Get-Location)

# Windows 无管理员权限时用 junction
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\wechat-export" -Target (Get-Location)
```

也可以直接复制目录。Claude Code 读取的是目录下的 `SKILL.md`；`scripts/`、`tests/` 一起复制即可。

## 快速开始

### 1. 安装底座

需要 Node.js 22.13+、Python 3.10+、已登录的微信桌面客户端。如果 `python --version` 打开 Microsoft Store，请安装 python.org 的 Python 并关闭应用执行别名。

```bash
npm install -g weflow-cli
weflow-cli setup --yes      # Python venv + sqlcipher3 等依赖
weflow-cli init             # 交互式：发现数据目录、提取/验证密钥
weflow-cli check
```

`weflow-cli init` 需要微信正在运行，并要求交互确认；请在终端里执行。

如果不想用 weflow-cli 的 venv，也可以自己建：

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
# macOS/Linux
.venv/bin/python -m pip install -r requirements.txt

node scripts/wechat42-export.mjs setup \
  --account-root "<账号目录>" \
  --venv-python ".venv\Scripts\python.exe"
```

> 4.2 默认走原生后端，导出不需要 `weflow-cli`；上面的 weflow-cli 安装只为 `--legacy-weflow` 回退/对照。4.2 原生只需要 Python venv 与 `work/`；`sqlcipher3` 只用于保留的 4.1 路径和 legacy sandbox。

> 下面的示例默认用 4.2 入口 `wechat42-export.mjs`；微信 4.1 把命令名换成 `wechat41-export.mjs`，参数相同。`wechat-export.mjs` 是内部主命令，调试时才直接用。

### 2. 体检

```bash
cd wechat-summary
node scripts/wechat42-export.mjs --check
```

### 3. 看看有哪些会话

```bash
node scripts/wechat42-export.mjs --list
```

### 4. 导出

```bash
# 某个群最近 7 天
node scripts/wechat42-export.mjs --group "项目群" --days 7

# 某个人某个月
node scripts/wechat42-export.mjs --person "张三" --from 2026-01-01 --to 2026-01-31

# 所有群最近 3 天，只要文字和视频
node scripts/wechat42-export.mjs --all-groups --days 3 --types text,video

# 精确指定会话 ID，输出到自定义目录
node scripts/wechat42-export.mjs --target "123@chatroom" --date 2026-01-05 \
  --output "D:\\wechat-exports\\项目群"
```

### 5. 联系人 / 跨会话 / 可选联网

```bash
# 联系人、群成员、头像
node scripts/wechat42-export.mjs contacts --output "D:\\wechat-exports\\contacts"

# 某人在所有会话的发言
node scripts/wechat42-export.mjs person --sender "张三" --output "D:\\wechat-exports\\person-张三"

# 全局时间线（跨所有会话）
node scripts/wechat42-export.mjs timeline --output "D:\\wechat-exports\\timeline"

# 只追加新消息（同一 --output 重复运行）
node scripts/wechat42-export.mjs timeline --output "D:\\wechat-exports\\timeline" --incremental

# 普通会话增量；媒体按内容 hash 去重
node scripts/wechat42-export.mjs --target "项目群" --incremental --output "D:\\wechat-exports\\项目群"
node scripts/wechat42-export.mjs check-incremental --output "D:\\wechat-exports\\项目群"

# 脱敏 + 加密 + 保留期（密码从环境变量读）
WECHAT_EXPORT_PASSWORD='***' node scripts/wechat42-export.mjs --target "项目群" \
  --redact strict --encrypt --remove-plaintext --retention-days 7 \
  --output "D:\\wechat-exports\\项目群"

# 解密 / 清理到期导出
WECHAT_EXPORT_PASSWORD='***' node scripts/wechat42-export.mjs decrypt-output \
  --input "D:\\wechat-exports\\项目群.wxenc" --output "D:\\wechat-exports\\项目群"
node scripts/wechat42-export.mjs retention-clean --output "D:\\wechat-exports"
node scripts/wechat42-export.mjs retention-clean --output "D:\\wechat-exports" --yes

# 公众号正文 / 本机缺失表情（显式联网，默认关闭）
node scripts/wechat42-export.mjs --target "群" --days 7 \
  --types text,link --fetch-articles --output "D:\\wechat-exports\\群"
node scripts/wechat42-export.mjs --target "群" --days 7 \
  --types emoji --download-stickers --output "D:\\wechat-exports\\群"

# 单个图片 .dat 解密
node scripts/wechat42-export.mjs decrypt-image "C:\\...\\xxx.dat" \
  --output "D:\\wechat-exports\\decoded.png"
```

## 微信 4.2（原生后端 + 可选 weflow-cli）

微信 4.2 的密钥是每库独立 raw key，`weflow-cli init` 的共享口令/Hook 路线不适用。本仓库提供 `scripts/wechat42-export.mjs`，核心已从 MIT 项目 [`TangGod991021/wechat-exporter`](https://github.com/TangGod991021/wechat-exporter) **vendor 到 `scripts/vendor/wechat42/`**：

1. 提取每库 raw key；
2. 解密到临时工作目录；
3. 用同一个 raw key+salt 重新加密到 sandbox（只在 `--legacy-weflow` 时需要）；
4. `msg/cache/resource` 用 junction 指回真实账号（媒体只读）；
5. 生成媒体映射补视频/图片/语音；V2 视频自动尝试解码；
6. **默认由 `native-backend.py` 直接读取 `work/`**，产出与 4.1 相同；不再调用 `weflow-cli`。原生 `--media` 已覆盖图片、文件、视频、语音、表情。

> **Level 2 现状（2026-11-18）**：4.1/4.2 默认走原生后端（4.1 `wechat41-decrypt.py`，4.2 `native-backend.py` + `native-weflow.mjs`），导出不再需要安装 `weflow-cli`。`--legacy-weflow` 可回退到旧路径做对照。
>
> 4.1 默认也走原生路径（`wechat41-decrypt.py` → `work/` → native backend）；4.2 `setup` 默认只生成 `work/`，`--with-sandbox` 才生成 sandbox（`--legacy-weflow` 或对照用）。

```bash
node scripts/wechat42-export.mjs setup \
  --account-root "C:\Users\<你>\...\xwechat_files\<账号目录>"

# 之后与普通导出相同（默认原生后端）
node scripts/wechat42-export.mjs --group "项目群" --days 7
node scripts/wechat42-export.mjs --target "<群ID>" --date 2026-11-03 --types all
node scripts/wechat42-export.mjs --all-official --days 7 --types all
node scripts/wechat42-export.mjs search --query 1684X --from 2026-08-17 --to 2026-11-17
node scripts/wechat42-export.mjs status
node scripts/wechat42-export.mjs doctor
node scripts/wechat42-export.mjs check-updates
node scripts/wechat42-export.mjs clean

# 回退到 weflow-cli 读取/导出（Level 2 对照）
node scripts/wechat42-export.mjs --legacy-weflow --group "项目群" --days 7
```

**本机实测（微信 4.2.0.13）**

| 日期 | 消息 | 文字 | 图片 | 文件 | 视频 | 语音 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-26 | 61 | 39 | 18（找到 13） | 4（找到 4） | 0 | 0 |
| 2026-11-03 | 33 | 25 | 6（找到 6） | 1（找到 1） | 1（找到 1，含封面） | 0 |
| 2026-11-13（SEx） | 47 | 43 | 0 | 0 | 0 | 4（找到 4，WAV） |

语音：2026-11-13 的 4 条语音全部解码为 WAV（`media/voice/`），HTML 用 `<audio>` 播放。4.2 语音需要：

```bash
python -m pip install silk-python
```

`pilk` 需要 MSVC 编译，Windows 上优先用 `silk-python`（`pysilk` 预编译 wheel）。语音数据在 `media_*.db` 的 `VoiceInfo`，不一定在 `media_0.db`。

4.1 加密库：默认用 `scripts/wechat41-export.mjs` 的原生路径（`wechat41-decrypt.py` 解密到独立 `work/`，再走 native backend；语音/表情/视频/文件都由原生 `--media` 处理）：

```bash
node scripts/wechat41-export.mjs --target "<群ID/wxid>" --date 2026-11-13 --types all
```

`--legacy-weflow` 可回退到旧的 weflow-cli 语音映射路径。原生路径需要 `sqlcipher3`（`python -m pip install sqlcipher3-wheels`）。

也可手动用 `wechat41-voice-map.py` + `--media-map` 调试（legacy 路径）。4.2 后端会自动生成语音映射。

### 表情

微信里的"表情"有两类，都已支持：

| 类型 | 表现 | 处理方式 |
| --- | --- | --- |
| 内置表情码 | 正文里的 `[玫瑰]`、`[捂脸]` | HTML/Markdown 渲染成对应 emoji（`🌹`、`🤦`）；JSONL 保留原始文字 |
| 自定义表情包 | 独立的"表情"消息（`localType 47`） | 解密后落盘到 `media/stickers/`（PNG/GIF），HTML 用 `<img>` 显示 |

自定义表情包是 AES-128-CBC 加密的（key = `md5(seed + wxid + "EMOTICON")`），seed 只存在于微信进程内存。导出时自动探测：优先用配置里的 `emoticonSeed`，否则扫微信进程内存（**需要微信正在运行**），命中后按样本解密校验。

```bash
# 手动拿种子（微信需在运行）
python scripts/wechat-sticker-seed.py --account-root "<xwechat_files>/<账号目录>"

# 指定种子导出
node scripts/wechat42-export.mjs --target "<群ID>" --days 7 --sticker-seed 206269640

# 不导出表情包（只保留文字标签）
node scripts/wechat42-export.mjs --target "<群ID>" --days 7 --no-sticker-map
```

微信的 `wxgf` 容器（H.265 裸流）需要 ffmpeg 转码：

```bash
python -m pip install imageio-ffmpeg
```

本机实测（2026-11-02，某个群）：21 条表情消息，18 条本机有缓存并成功解密（PNG 3 / GIF 15），3 条本机没有缓存（微信从未下载或已清理），记为缺失而非猜测。

`clean` 会删除 `sandbox/`、明文 `work/`、`home/` 和 `baseDir/_stickers`，但保留导出结果；下次导出会重新构建。4.1 用 `node scripts/wechat41-export.mjs clean` 删除 `wechat41/work`、`_stickers` 和映射临时文件。4.2 的 `<baseDir>/keys.json` 是离线复用用的逐库 raw key 缓存，默认保留；需要删除时用 `node scripts/wechat42-export.mjs clean --purge-keys`。

### 完整性校验

每次 4.2 导出后会自动校验（`work/` 存在时）；4.1 在显式传 `--output` 时自动校验。校验链是：

```text
数据库该时间窗内的行数
  == weflow-cli 返回的原始 JSON 条数
  == messages.jsonl 条数 + 按类型跳过条数
```

同时检查：

- 重复的 `localId:createTime`
- 导出记录是否落在请求的时间窗内
- `coverage.mayHaveMore` 是否为 true
- `messages.jsonl` 引用的媒体文件是否真的在磁盘上

结果写到导出根目录的 `audit.json`，并在终端打印摘要。不完整时退出码 **5**（产物仍然保留，便于排查）。跳过用 `--no-audit`。

手动校验：

```bash
# 4.2（需要 work/；clean 后先 setup）
node scripts/wechat42-export.mjs audit --output "<导出目录>"

# 4.1
node scripts/wechat41-export.mjs audit --output "<导出目录>"
```

### 深度对账（`--deep`）

计数一致不等于内容一致：漏一条、重复一条可能让总数刚好相等。`--deep` 会重新读取 `work/` 源库，按消息身份 `(local_id, server_id)` 逐条对账：

```text
源库重读的消息 ID 集合  A
原始 JSON 的消息 ID 集合  B
messages.jsonl 的 ID 集合 C

missing_ids   = A - B      # 源库有、原始 JSON 没有
extra_ids     = B - A      # 原始 JSON 有、源库没有
exported_extra = C - B     # 导出有、原始 JSON 没有
```

同时检查 `B - C` 的条数是否等于按类型跳过数，以及导出记录的 `type` 是否都在 `--types` 里。报告写进 `audit.json` 的 `deep` 字段，缺失或多余时退出码 **5**。

```bash
node scripts/wechat42-export.mjs audit --output "<导出目录>" --deep
node scripts/wechat41-export.mjs audit --output "<导出目录>" --deep
```

深度对账需要一个**导出时刻的边界**，避免把导出后新到的消息误报为缺失。原生后端会把 DB 里最新的 `(create_time, local_id)` 写进原始 JSON 的 `coverage.boundary`；旧导出没有这个字段时会退回 `raw-max` / `generatedAt` 并在报告里标 `reliable: false`，尾部消息可能漏检，重新导出即可获得可靠边界。`--deep` 只支持已解密的 `work/`（4.1/4.2 原生路径）；legacy `weflow` 模式需要先跑一次原生导出。

校验只能证明"数据库里有、导出里也有"；本机从未缓存/已被微信清理的媒体仍会如实记为缺失，不能凭空恢复。

### 撤回记录

微信的撤回分两种，能拿到的程度不同：

| 类型 | 本机能否拿到原内容 | 说明 |
| --- | --- | --- |
| 批量撤回 | **能** | `general.db` 的 `revokebatchmessage` 记录 `msg_local_id`/`msg_create_time`，原消息行通常还在 |
| 单条撤回 | 通常**不能** | 消息表只剩 `sysmsg type="revokemsg"` 提示，没有 `newmsgid`；原行已删除 |
| 单条撤回（`revokemessage` 有记录） | 可能 | 该表 `content` 字段可能带原文；本机 0 行，无法验证 |

4.2/4.1 导出时自动读取撤回痕迹（`--no-revoke-map` 关闭）：

- 能对上的消息在 `messages.jsonl` 里加 `revoked: true` 和 `revoke`（类型、批次、server_id 等）；
- HTML 显示“已撤回”徽标，Markdown 标注“（已撤回）”；
- 每个会话目录生成 `revokes.json`：匹配项 + 无法对应本地消息的 `orphans`（含 `content`，如果撤回表提供）。

本机实测（2026-11-17，某群）：2 条批量撤回全部对上，原文件消息完整导出并标记；970 条普通单条撤回只有提示，没有原文。4.1 的撤回读取代码路径已实现，但本机无 4.1 数据，尚未真机验证。

## 输出

```text
<输出目录>/
├── index.html
├── manifest.json
├── audit.json            # 完整性校验报告（数据库/原始/导出；--deep 含逐条 ID 对账）
├── README.txt
└── <名称>_<id前8位>/
    ├── index.html          # 可按类型/发送者/关键词筛选的聊天页
    ├── transcript.md       # 按日期分组的 Markdown
    ├── messages.jsonl      # 一行一条，适合脚本/模型
    ├── messages.json
    ├── manifest.json
    ├── README.txt
    ├── revokes.json        # 撤回记录（批量/单条、匹配与孤立）
    ├── raw/                # weflow-cli 原始产物（可 --prune-raw）
    └── media/
        ├── images/
        ├── files/
        ├── videos/
        ├── voice/              # 语音 WAV（4.2）
        ├── stickers/           # 表情包 PNG/GIF
        └── index.json
```

`messages.jsonl` 示例（已截断）：

```json
{"id":"2:1767578460","timestamp":1767578460,"localTime":"2026-01-05 10:01:00","sender":"李四","isMe":false,"type":"image","text":"[图片]","media":{"image":"media/images/img_2_fake.jpg"},"missing":[]}
{"id":"3:1767578520","timestamp":1767578520,"localTime":"2026-01-05 10:02:00","sender":"李四","isMe":false,"type":"file","text":"[文件] 报告.pdf","fileName":"报告.pdf","media":{"file":"media/files/报告.pdf"},"missing":[]}
{"id":"4:1767578580","timestamp":1767578580,"localTime":"2026-01-05 10:03:00","sender":"李四","isMe":false,"type":"video","text":"[视频 12″]","media":{"video":"media/videos/video_4_abcdef012345.mp4","poster":"media/images/poster_4_abcdef01.jpg"},"missing":[]}
```

## 常用选项

| 选项 | 作用 |
| --- | --- |
| `--target / --group / --person` | 指定会话，可重复；同名时用 ID |
| `--all-groups / --all-persons` | 批量导出群/个人 |
| `--all-official` | 批量导出公众号（`gh_`）会话 |
| `--from/--to`、`--date`、`--days` | 时间范围；`--to` 含当天 |
| `--types` | `text,image,file,video,voice,emoji,link,history,quote,transfer,redpacket,location,card,...,all`；默认 `text,image,file,video,voice,emoji,link,history,quote,transfer,redpacket,location,card,call` |
| `--media-strategy` | `auto` / `day` / `full` / `none` |
| `--full-images` | 用原图而不是缓存缩略图（慢） |
| `--no-video-scan` | 不扫描 `msg/video` |
| `--video-root` | 指定微信账号目录或视频目录 |
| `--fetch-articles` | 显式联网抓取公众号正文（仅 `mp.weixin.qq.com`） |
| `--download-stickers` | 显式联网下载本机缺失的表情（Tencent 白名单） |
| `--with-media-map` | native 默认跳过冗余 media-map；加此参数强制生成 |
| `--output` | 输出目录（默认 `~/.wechat-export/<时间戳>`） |
| `--json` | stdout 输出机器可读结果 |
| `--dry-run` | 只打印计划，不写文件 |
| `--allow-partial` | 部分目标/媒体失败时继续 |
| `--prune-raw` | 成包后删除 `raw/` |

完整参数见 `node scripts/wechat-export.mjs --help`。

## 媒体策略为什么不是一句话

`weflow-cli export md` 的图片/文件抽取只支持 `--date`（单日），不支持 `--from/--to`。所以：

- 有图片/文件的日期 ≤31 天：先拿到消息，找出**真正有图片/文件**的日期，逐日调用 `md`，再读合并后的 `manifest.json`；
- 有媒体的日期更多：一次全量抽取，并在结果里明确告警；
- 只要文字/视频：完全不碰 Python 媒体链路。

视频单独处理：微信把视频存成 `msg/video/YYYY-MM/<md5>.mp4`（自发视频可能是 `<md5>_raw.mp4`）。skill 从消息 XML 的 `md5/rawmd5` 匹配文件并复制；不做猜测，找不到就写 `missing` 原因。

图片/文件在 `raw/` 与 `media/` 之间优先硬链接（`raw/` 本身已是 weflow-cli 写出的副本）；视频从微信 `msg/video` **复制**，避免编辑导出文件时改到源文件。视频多时会明显变慢、占空间，建议输出到空间充足的磁盘。

## 环境要求

- Windows 10/11 + 微信 **4.2.x**：当前维护
- 微信 4.1 / 3.x：代码保留或尽力兼容，但不在当前维护/验收范围
- Node.js 22.13+；Python 3.10+
- Python 依赖：`python -m pip install -r requirements.txt`
  - 语音：`silk-python`（`pysilk` 预编译 wheel）；`pilk` 需要 MSVC，不作为首选
  - 表情包：`imageio-ffmpeg`（`wxgf` → H.265 → PNG/GIF）+ `cryptography`
  - 4.1 保留路径 / legacy sandbox：可选 `sqlcipher3-wheels`
- `weflow-cli` 仅 `--legacy-weflow` 需要；4.2 默认原生后端不走 `weflow-cli init`。

## 测试

```bash
npm test
```

当前 81 项 Node 测试 + 62 项 Python 测试，覆盖：

- 参数解析、日期窗口、类型映射
- 文件 appmsg / 视频 md5 / 群发送者前缀 / 链接 fallback
- 视频目录扫描、`_raw` 优先、文件头校验、V2 解码
- 语音 SILK→WAV（pysilk 合成）
- 表情包解密、wxgf 转码、内置表情码渲染
- 完整性校验：数据库行数 = 原始 JSON = 导出 + 按类型跳过；缺行/重复/时间窗外可检出；`--deep` 按 `local_id+server_id` 逐条对账（含“计数相等但一丢一重”）
- 撤回记录：批量撤回匹配原消息、单条撤回孤立记录、HTML/Markdown 标记
- 引用/合并转发：`refermsg` → `quote`、`recorditem` → `records`；HTML/Markdown 渲染
- 转账/红包/位置/名片：金额、标题、经纬度、名片账号进入 JSONL；HTML/Markdown 结构化展示
- 联系人/群成员/头像、跨会话 person/timeline、按会话 `--incremental` 状态追加与媒体 hash 去重
- 小程序字段、合并转发内层媒体、V2 图片/WXGF、脱敏、加密包、保留期清理
- 公众号正文/表情 CDN 的域名白名单、文章正文提取、单文件 `decrypt-image`
- 文本型导出跳过媒体/表情映射（不扫媒体、不扫 sticker seed）
- 只读命令白名单、输出路径保护
- Level 2 原生 4.2 后端：sessions/contacts/export json（含图片/文件）
- 端到端：假 `weflow-cli` + 合成微信目录，验证文本/图片/文件/视频/语音/表情、引用/合并转发、转账/红包/位置/名片、链接 fallback、缺失原因、dry-run、列表过滤、公众号批量、环境体检、输出目录保护

## 目录

```text
SKILL.md                     # Agent 使用说明（触发词、流程、排障）
STATUS.md                    # 当前功能、限制与按优先级排列的待办
TODO.md                      # 当前待办（P0/P1/P2；不含 GUI/工程化界面）
README.md / DESIGN.md        # 说明与设计
BACKLOG.md                   # 已记录、暂不实现的需求（收藏等）
SECURITY.md                  # 安全边界、密钥/网络/本地产物
VERSION_MAINTENANCE.md       # 微信升级排查手册
REFERENCES.md                # 参考项目清单
THIRD_PARTY_NOTICES.md       # 第三方代码/依赖/许可证
NOTICE                       # 版权与来源声明
docs/MERGE_PLAN.md           # 分支收敛记录（已完成；只剩 main）
docs/OUTPUT_SCHEMA.md        # messages/manifest/audit/contacts 字段定义
requirements.txt             # Python 依赖（4.2 原生/语音/表情）
compat.json                  # 微信版本 → 上游 commit → 验证结果
scripts/
  wechat-export.mjs          # 内部主命令（4.1/4.2 wrapper 调用；调试用）
  native-weflow.mjs          # Level 2：weflow-cli 兼容 shim（4.1/4.2 默认）
  native-backend.py          # Level 2：原生读取/导出（sessions/contacts/export）
  wechat-contacts-export.py  # 联系人/群成员/头像导出
  wechat-decrypt-image.py    # 单文件图片 .dat/V2 解密
  wechat-person-export.py    # 跨会话按人/全局时间线/增量
  wechat-incremental-check.mjs # 增量状态/计数/媒体去重校验
  wechat-decrypt-output.mjs   # 解密 .wxenc 加密包
  wechat-retention-clean.mjs  # 按 manifest.retention 清理到期导出
  check.mjs                  # 环境体检入口
  wechat42-export.mjs        # 微信 4.2 后端入口
  wechat41-export.mjs        # 微信 4.1 入口（默认原生；--legacy-weflow 回退）
  wechat41-decrypt.py        # 4.1 SQLCipher → work/（原生路径）
  wechat42-sandbox.py        # 4.2 解密 + 可选重加密 sandbox
  wechat42-media-map.py      # 4.2 媒体映射（回退/补充）
  wechat42-bridge.py         # 4.2 单会话直接导出（调试/最小验证）
  wechat42-search.py         # 4.2 关键词搜索
  wechat41-voice-map.py      # 4.1 legacy 语音映射
  wechat-sticker-map.py      # 表情包媒体映射（4.1/4.2）
  wechat-sticker-seed.py     # 表情包解密种子探测
  wechat-audit.py            # 完整性校验（数据库/原始/导出 对账；--deep 逐条 ID 对账）
  wechat-revoke-map.py       # 撤回记录映射（批量/单条）
  v2-decode.py               # V2 加密视频解码（独立入口）
  python-tests.mjs           # Python 测试 runner
  vendor/wechat42/           # MIT vendor：keys/decrypt/parser/export/media
  lib/
    v2_decode.py             # V2 AES-ECB + XOR 解码
    voice_decode.py          # SILK→WAV
    sticker.py               # 表情包 AES-CBC 解密 + wxgf 转码
    v41db.py                 # 4.1 SQLCipher 解密/导出
    faces.mjs                # 内置表情码 → emoji
    wechat_faces.json        # 内置表情码映射表
    args.mjs                 # 参数解析
    weflow.mjs               # weflow-cli 调用与 JSON 解析
    config.mjs               # 微信目录推导、账号分类
    time.mjs                 # 时间窗口
    parse.mjs                # 消息/XML 解析与归一化
    video.mjs                # 视频索引与匹配
    media.mjs                # 文件命名、硬链接/复制
    bundle.mjs               # 单会话打包
    html.mjs                 # HTML 浏览页
    markdown.mjs             # Markdown 转写
tests/
  fixtures/fake-weflow.mjs   # 测试用假 CLI
  *.test.mjs
```

## 后续微信版本升级

> 不限于 4.3/4.4；以后任何微信版本升级都按同一套层和方法排查。

升级后**先查参考库，不要从零重写**。完整矩阵和入口函数见：

- [`VERSION_MAINTENANCE.md`](VERSION_MAINTENANCE.md) → 4.2 升级排查手册
- `SKILL.md` → “后续微信版本升级参考”
- `DESIGN.md` → “13. 后续微信版本升级参考库与排查手册”

速查：

| 层 | 优先参考 |
| --- | --- |
| 密钥提取 | `TangGod991021/wechat-exporter`（MIT）、`SmileGrassSky620/wechat-local-reader`、`fangtimmy/wechat4-db-decrypt`、`anpingjing/wechat-chatlog-decrypt-export`、`TANGandXUE/wcdb-key-tool` |
| DB 解密/读取 | `Tencent/wcdb`（BSD-3）、`sqlcipher3`/`sqlcipher3-wheels`、`pycryptodome`/`cryptography` |
| Schema/解析 | `zhuobichen/weflow-cli`（MIT）、`wechat-exporter` 的 `message_parser.py`/`export.py`、WCDB/sqlite3 `PRAGMA table_info` |
| 媒体 | `weflow-cli/scripts/media_index.py`、`export_chat_html.py`、`wechat-exporter/app/core/export.py`、`qilaidev/wechat-localmind`（MIT） |
| 导出/HTML | 本仓库 `bundle.mjs`/`html.mjs`/`markdown.mjs` |

> WCDB 只帮解密/读库，不提供微信密钥。WeFlow 是 CC BY-NC-SA 4.0，不建议直接 vendor 到商用代码。chatlog/PyWxDump/MemoTrace/wechat-dump-rs 已删库或停更，只能作历史参考。

## 隐私与合规

- 只处理本人或明确获授权访问的数据。
- 默认本机处理，不上传任何内容。
- 输出目录含真实聊天与第三方个人信息；不要提交 Git、网盘或公开链接。
- 不用于监控、跟踪或读取他人微信；不要部署到他人设备或远程静默运行。
- 使用前自行确认当地法律、微信服务条款与账号风控风险。

## 许可证

- 本仓库：**MIT**（根 `LICENSE`）。
- `scripts/vendor/wechat42/`：来自 MIT 项目 [`TangGod991021/wechat-exporter`](https://github.com/TangGod991021/wechat-exporter)，保留其 `LICENSE` 与 `NOTICE.md`。
- 读取底座 `weflow-cli`：MIT（仅 `--legacy-weflow` 可选）。
- `WeFlow`：CC BY-NC-SA 4.0，仅作设计参考，未复制代码。
- 完整第三方清单见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 参考

- [`REFERENCES.md`](REFERENCES.md)：参考项目、许可证和升级排查用法
- [weflow-cli](https://github.com/zhuobichen/weflow-cli)：legacy 读取/导出底座
- [WeFlow](https://github.com/hicccc77/WeFlow)：本地 GUI，查看、分析、多格式导出与 HTTP API
- [chatlog](https://github.com/sjzar/chatlog)：Go 实现，HTTP API + MCP，支持媒体
- PyWxDump / MemoTrace：历史工具，截至 2026 年已出现删库/停更，不建议作为新依赖
