# wechat-export

把本机微信桌面客户端的聊天记录导出成一个可直接浏览、也可喂给脚本/模型的目录：**文字、图片、传输文件、视频**，支持**按时间、按群、按个人**筛选。

> 仅用于用户本人设备上、本人已登录并有权访问的微信数据。导出内容含第三方个人信息，请勿上传或公开。

当前功能、已知限制和待办见 **[STATUS.md](STATUS.md)**；设计细节见 `DESIGN.md`，暂不实现的需求见 `BACKLOG.md`。

## 它解决什么

微信客户端本身不提供“按群/按人/按时间段导出成文件”的能力。已有工具大体分两类：

- CLI / 自动化：解密、查询、导出，但不一定导出视频本体，也不方便按范围批量编排；
- GUI：浏览体验好，但难以在 Claude Code 等 Agent 里稳定调用。

本 skill 把两者之间缺的那层补上：

```text
微信本地库
  └─ weflow-cli（解密、查询、图片/文件定位）
       └─ wechat-export skill（目标/时间/类型编排、视频落盘、统一产物）
            └─ index.html + transcript.md + messages.jsonl + media/
```

## 当前范围

**已包含**

- 文字消息、图片、传输文件、视频、文章链接
- 语音音频（4.2 自动；4.1 用 `wechat41-export.mjs`）
- 表情：内置表情码渲染成 emoji；自定义表情包解密导出为 PNG/GIF
- 撤回记录：批量撤回匹配原消息；单条撤回/孤立记录标记
- 按时间（`--from/--to/--date/--days`）、按群、按个人
- 公众号批量导出（`--all-official`）
- 微信 4.1 及以下（`weflow-cli`）与微信 4.2（`wechat42-export.mjs`，核心已 vendor）
- V2 加密视频自动解码
- HTML / Markdown / JSONL / manifest / media 包
- `doctor` / `compat.json` / `check-updates`

**暂不包含**（已记录在 `BACKLOG.md`，后续再决定）

- 微信收藏
- 朋友圈
- 公众号文章正文抓取
- 4.1 语音自动映射的真机验证
- 语音转写
- 运行时完全脱离 `weflow-cli`（Level 2；Level 1 vendor 已完成）
- **写入、转发、新增、删除、编辑、撤回聊天内容**

## 只读边界

本 skill 只读、只解析、只导出副本。运行时强制：

- **weflow-cli 命令白名单**：只允许 `check` / `sessions` / `contacts` / `media` / `export json|md`。`send`、`forward`、`delete`、`edit`、`revoke`、`config`、`fav`、`assistant` 等在 spawn 前就被拒绝（`WEFLOW_READ_ONLY_VIOLATION`）。
- **输出路径保护**：导出目录、4.2 的 `baseDir/work/sandbox/home`、表情/语音输出都不能位于微信账号目录内；Node 与 Python 两侧都检查，违反直接失败。
- **复制语义**：图片/文件硬链接的是 `raw/` 导出副本；视频、表情包复制，不硬链接微信源文件。
- **无上传**：4.2 密钥提取只读进程内存；`check-updates` 只做 `git ls-remote`。

唯一会写的是导出目录和 `~/.wechat-export/` 下的临时工作目录。`doctor` 的 `readOnly` 字段可用来审计。

## 安装为 Claude Code Skill

把整个目录复制或链接到用户级 skill 目录：

```bash
# macOS / Linux
ln -s "$(pwd)" ~/.claude/skills/wechat-export

# Windows PowerShell（管理员或开发者模式）
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\wechat-export" -Target (Get-Location)
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

### 2. 体检

```bash
cd wechat-summary
node scripts/wechat-export.mjs --check
```

### 3. 看看有哪些会话

```bash
node scripts/wechat-export.mjs --list
```

### 4. 导出

```bash
# 某个群最近 7 天
node scripts/wechat-export.mjs --group "项目群" --days 7

# 某个人某个月
node scripts/wechat-export.mjs --person "张三" --from 2026-01-01 --to 2026-01-31

# 所有群最近 3 天，只要文字和视频
node scripts/wechat-export.mjs --all-groups --days 3 --types text,video

# 精确指定会话 ID，输出到自定义目录
node scripts/wechat-export.mjs --target "123@chatroom" --date 2026-01-05 \
  --output "D:\\wechat-exports\\项目群"
```

## 微信 4.2（独立后端）

微信 4.2 的密钥是每库独立 raw key，`weflow-cli init` 的共享口令/Hook 路线不适用。本仓库提供 `scripts/wechat42-export.mjs`，核心已从 MIT 项目 [`TangGod991021/wechat-exporter`](https://github.com/TangGod991021/wechat-exporter) **vendor 到 `scripts/vendor/wechat42/`**：

1. 提取每库 raw key；
2. 解密到临时工作目录；
3. 用同一个 raw key+salt 重新加密到 sandbox，让 `weflow-cli` 能读所有分片；
4. `msg/cache/resource` 用 junction 指回真实账号（媒体只读）；
5. 生成媒体映射补视频/图片/语音；V2 视频自动尝试解码；
6. 运行标准导出流程，产物与 4.1 相同。

> 默认使用内置 vendor，不需要 clone；`--tool-dir` 可指向外部上游做更新验证。上游可能像 chatlog/PyWxDump 一样被下架，vendor 副本降低这个风险。

```bash
node scripts/wechat42-export.mjs setup \
  --account-root "C:\Users\<你>\...\xwechat_files\<账号目录>"

# 之后与普通导出相同
node scripts/wechat42-export.mjs --group "项目群" --days 7
node scripts/wechat42-export.mjs --target "<群ID>" --date 2026-11-03 --types all
node scripts/wechat42-export.mjs --all-official --days 7 --types all
node scripts/wechat42-export.mjs search --query 1684X --from 2026-08-17 --to 2026-11-17
node scripts/wechat42-export.mjs status
node scripts/wechat42-export.mjs doctor
node scripts/wechat42-export.mjs check-updates
node scripts/wechat42-export.mjs clean
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

4.1 加密库语音：用 `scripts/wechat41-export.mjs`（自动生成 voice map 后调用标准导出）：

```bash
node scripts/wechat41-export.mjs --target "<群ID/wxid>" --date 2026-11-13 --types all
```

也可手动用 `wechat41-voice-map.py` + `--media-map` 调试。4.2 后端会自动生成语音映射。

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

`clean` 会删除 `sandbox/`、明文 `work/` 和含 sandbox 密钥的 `home/`，但保留导出结果；下次导出会重新构建。

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
├── audit.json            # 完整性校验报告（数据库/原始/导出 对账）
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
| `--types` | `text,image,file,video,voice,link,emoji,...,all`；默认 `text,image,file,video,voice,link` |
| `--media-strategy` | `auto` / `day` / `full` / `none` |
| `--full-images` | 用原图而不是缓存缩略图（慢） |
| `--no-video-scan` | 不扫描 `msg/video` |
| `--video-root` | 指定微信账号目录或视频目录 |
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

- Windows 10/11 + 微信 4.x：重点支持
- 微信 3.x：尽力兼容（视频目录按 `FileStorage/Video` 兜底）
- Node.js 22.13+；Python 3.10+（仅图片/文件抽取需要）
- 语音导出需要 `python -m pip install silk-python`（`pysilk` 预编译 wheel）；`pilk` 需要 MSVC，不作为首选
- 表情包导出需要 `python -m pip install imageio-ffmpeg`（`wxgf` → H.265 → PNG/GIF）与 `cryptography`
- `weflow-cli` 的 `check` 应显示消息库可读
- `weflow-cli` 当前验证到微信 4.1.15.56；微信 **4.2** 使用独立的 `wechat42-export.mjs` 后端（见上文），不再走 `weflow-cli init`。

## 测试

```bash
npm test
```

当前 47 项 Node 测试 + 25 项 Python 测试，覆盖：

- 参数解析、日期窗口、类型映射
- 文件 appmsg / 视频 md5 / 群发送者前缀 / 链接 fallback
- 视频目录扫描、`_raw` 优先、文件头校验、V2 解码
- 语音 SILK→WAV（pysilk 合成）
- 表情包解密、wxgf 转码、内置表情码渲染
- 完整性校验：数据库行数 = 原始 JSON = 导出 + 按类型跳过；缺行/重复/时间窗外可检出
- 撤回记录：批量撤回匹配原消息、单条撤回孤立记录、HTML/Markdown 标记
- 只读命令白名单、输出路径保护
- 端到端：假 `weflow-cli` + 合成微信目录，验证文本/图片/文件/视频/语音/表情、链接 fallback、缺失原因、dry-run、列表过滤、公众号批量、环境体检、输出目录保护

## 目录

```text
SKILL.md                     # Agent 使用说明（触发词、流程、排障）
STATUS.md                    # 当前功能、限制与按优先级排列的待办
README.md / DESIGN.md        # 说明与设计
BACKLOG.md                   # 已记录、暂不实现的需求（收藏等）
compat.json                  # 微信版本 → 上游 commit → 验证结果
scripts/
  wechat-export.mjs          # 主命令（4.1 / weflow-cli）
  check.mjs                  # 环境体检入口
  wechat42-export.mjs        # 微信 4.2 后端入口
  wechat41-export.mjs        # 微信 4.1 入口（自动语音映射）
  wechat42-sandbox.py        # 4.2 解密 + 重加密 sandbox
  wechat42-media-map.py      # 4.2 视频/图片/语音媒体映射
  wechat42-bridge.py         # 4.2 单会话直接导出（调试/最小验证）
  wechat42-search.py         # 4.2 关键词搜索
  wechat41-voice-map.py      # 4.1 加密库语音映射
  wechat-sticker-map.py      # 表情包媒体映射（4.1/4.2）
  wechat-sticker-seed.py     # 表情包解密种子探测
  wechat-audit.py            # 完整性校验（数据库/原始/导出 对账）
  wechat-revoke-map.py       # 撤回记录映射（批量/单条）
  v2-decode.py               # V2 加密视频解码（独立入口）
  python-tests.mjs           # Python 测试 runner
  vendor/wechat42/           # MIT vendor：keys/decrypt/parser/export/media
  lib/
    v2_decode.py             # V2 AES-ECB + XOR 解码
    voice_decode.py          # SILK→WAV
    sticker.py               # 表情包 AES-CBC 解密 + wxgf 转码
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
- 读取底座 `weflow-cli`：MIT。
- `WeFlow`：CC BY-NC-SA 4.0，仅作设计参考，未复制代码。

## 参考

- [weflow-cli](https://github.com/zhuobichen/weflow-cli)：本项目的解密/查询/导出底座
- [WeFlow](https://github.com/hicccc77/WeFlow)：本地 GUI，查看、分析、多格式导出与 HTTP API
- [chatlog](https://github.com/sjzar/chatlog)：Go 实现，HTTP API + MCP，支持媒体
- PyWxDump / MemoTrace：历史工具，截至 2026 年已出现删库/停更，不建议作为新依赖
