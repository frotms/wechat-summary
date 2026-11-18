---
name: wechat-chatlog
description: 只读读取并导出本机微信 4.x 聊天记录（文字、图片、视频、文件、语音），支持按时间、群聊、私聊、发送者、关键词和消息类型筛选。当用户要求查看、搜索、汇总或归档微信聊天内容时使用。全程不截图；密钥缺失时需微信已登录，用只读内存扫描取 key。
---

# 微信聊天记录读取与导出（只读）

用 SQLCipher 独立解密 `db_storage` 的**副本**，不调用 `wcdb_api.dll`、不截图、
不走协议、不写微信任何文件。媒体从微信本地缓存读取/解密，导出到指定目录。

## 前置条件

1. Windows + Python 3（依赖 `cryptography`、`zstandard`；可选 `sqlcipher3`，
   可选 `pilk` 用于 SILK→WAV，见 `requirements.txt`）。
2. 数据目录：`~/xwechat_files/<账号>/db_storage`。
3. **该账号的数据库密钥**。解析顺序：
   - `--key-file <文件>`（64 位 hex 或 32 字符）
   - 环境变量 `WECHAT_DB_KEY`
   - 本目录 `keys.json`：`{"accounts": {"<账号目录名>": "<64位hex>"}, "default": "..."}`
   - WeFlow 配置里的 `decryptKey`（仅当对该账号校验通过）
   - **兜底：只读扫描正在登录的 `Weixin.exe` 内存**（`OpenProcess + ReadProcessMemory`，
     不注入、不 hook、不修改进程），从 WCDB 配置对象取每个库的 32 字节原始密钥。
     要求微信已登录；密钥只在内存中使用，不打印、不落盘。

   密钥只用不打印。`python wfchat.py doctor` 显示每个账号的密钥状态
   （`ok` / `raw-scan` / `missing`）。

**自动取 key（当前登录账号）**：没有存储密钥时，`wfchat.py` 会对正在运行的
`Weixin.exe` 做一次只读扫描：

- 找 `com.Tencent.WCDB.Config.Cipher` 配置对象，按固定掩码 XOR 得到
  `x'<64hex>'`，验证每个库的 SQLCipher HMAC，得到逐库 raw key；
- 复制 `db + db-wal + db-shm` 后用 sqlcipher3 打开，能读到 WAL 中的最新消息；
- 图片 key 由 `weixin.dll` cfg 对象的 `cfgDword` 现场派生。

扫描只读（`OpenProcess/ReadProcessMemory/VirtualQueryEx`），不注入、不 hook、
不 patch；key 只在进程内使用，不打印、不写盘。

## 命令

脚本：`.claude/skills/wechat-chatlog/wfchat.py`（全局参数 `--account`、`--key-file`）

```bash
python wfchat.py doctor
python wfchat.py --account <账号> diagnose      # 微信版本/DLL 哈希/schema 指纹/媒体/key 方式
python wfchat.py sessions --limit 30 [--kind group|private|official]
python wfchat.py messages --session "<标题或username>" --since 2026-11-14 --until 2026-11-15
python wfchat.py search --keyword 关键词 --since 2026-11-01 [--until 2026-11-17]
python wfchat.py media --session "<标题>" --since 2026-11-14
python wfchat.py clean [--dry-run]             # 删除工作目录里的解密明文副本
python wfchat.py verify --out <导出目录> [--deep]  # 检查导出是否完整
python selftest.py                             # 合成数据自检（不碰个人数据）
python regression.py --account <账号> --last 30  # 真实数据：导出 + verify --deep + 媒体头校验
```

### 导出（本次新增）

```bash
# 单个群/个人，按时间 + 类型
python wfchat.py export --session "群名或wxid" \
  --since 2026-08-01 --until 2026-08-31 \
  --types text,image,video,file --format html,json,md,txt

# 只要某个群成员的发言
python wfchat.py export --session "群名" --sender "张三" --since 2026-08-01

# 批量：全部群聊，最近 3 天，最多 20 个会话
python wfchat.py export --all --kind group --since 2026-08-18 --limit-sessions 20

# 只索引媒体、不复制大文件
python wfchat.py export --session "群名" --media-mode index
```

主要参数：

| 参数 | 说明 |
|---|---|
| `--session` | 标题片段或完整 username（`xxx@chatroom`/`wxid_xxx`/`gh_xxx`）；可重复或逗号分隔 |
| `--all` | 导出全部匹配会话；**必须显式加**，否则只处理 `--session` |
| `--kind` | `group` / `private` / `official` / `all`（会话级筛选） |
| `--since` / `--until` | `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM[:SS]`；`--until` 日期含当天 |
| `--types` | `text,image,video,file,voice,sticker,app,system` |
| `--sender` | 按发送者显示名/wxid 片段筛选（群聊按个人导出） |
| `--keyword` / `--last N` | 关键词、最近 N 条 |
| `--format` | `html,json,md,txt` 任意组合（默认 `html,json`） |
| `--media-mode` | `copy`（默认，落盘）/ `index`（只记录路径）/ `none`（不处理媒体） |
| `--voice-format` | `both`（默认，SILK+WAV）/ `wav` / `silk` |
| `--fetch-articles` | 显式联网抓取公众号文章正文（仅 `mp.weixin.qq.com`），默认关闭 |
| `--download-stickers` | 显式联网尝试下载本地缺失的表情，默认关闭 |
| `--max-media-mb` | 单文件大小上限，0 表示不限 |
| `--out` | 输出目录；默认 `$WECHAT_WORK_DIR/export_<时间>`（默认 `~/.wechat-chatlog/work`） |
| `--flat` | 所有会话写入同一目录 |

## 输出结构

```
<out>/
  index.html                     # 批量导出目录页
  <会话标题>_<username>/          # 每个会话一个目录
    chat.html                    # 聊天界面，图片/视频内嵌，文件可点击
    chat.json                    # 会话元数据 + 筛选条件 + 媒体统计
    chat_messages.json           # 逐条消息（含 media: rel/status/source/size）
    chat.md / chat.txt           # 可选
    manifest.json                # 媒体状态、图片密钥状态、警告
    media/
      images/YYYY-MM-DD/...      # 解密后的图片
      videos/YYYY-MM-DD/...      # 原样复制的 mp4
      files/YYYY-MM-DD/...       # 原样复制的文件（保留原名）
      voices/YYYY-MM-DD/...      # SILK + WAV（装了 pilk 时）
      stickers/YYYY-MM-DD/...    # 表情（本地缓存或 --download-stickers）
      articles/YYYY-MM-DD/...    # 公众号正文 .txt/.html（--fetch-articles）
```

媒体 `status`：`exported` / `locked`（图片缺 AES 密钥）/ `missing`（本地没缓存）/
`indexed` / `too_large` / `error`。全部写入 `manifest.json` 与消息 JSON，HTML 里也会显示。

### 完整性检查

导出时每个会话的 `manifest.json` 会写 `checks`：

- `source_message_count` / `exported_message_count` / `filtered_out`
- `media_message_count` / `media_status`
- `unhandled_kinds`（未识别的消息类型）
- `missing_rel` / `zero_size_rel`（引用了但磁盘不存在/为空的媒体）
- `ok`（false 时导出过程中会打印 `[CHECK FAILED]`）

导出后可再跑：

```bash
python wfchat.py verify --out <目录>          # 检查消息数、媒体文件、状态
python wfchat.py --account <账号> verify --out <目录> --deep
```

`--deep` 会重新读取微信源库，按 `local_id + server_id` 对比导出的消息，
报告 `missing_ids` / `extra_ids`；这是“有没有漏消息”的最终校验。

## 媒体读取规则（微信 4.x，本机已验证）

- **图片**：先查微信自己的**解码缓存**（明文，非截图）：
  `cache/<月>/Message/<md5(会话)>/ImageTemp/<local_id>_<时间>_hd_temp`
  或 `.../Thumb/<local_id>_<时间>_thumb.jpg`。命中就直接复制，不需要图片密钥。
  未命中再看 `msg/attach/<dir1>/<dir2>/Img/<md5>.dat`（由 `hardlink.db` 的
  `image_hardlink_info_v4` 定位；优先 `_h.dat`、`.dat`、`_t.dat`）。
  4.x 新消息的 XML md5 可能与真实文件名不同，此时用 `message_resource.db` 的
  `MessageResourceInfo.packed_info`（含 32 位文件 hash）还原。
  V2 格式 = 14 字节头 + AES-128-ECB 段 + 明文段 + 单字节 XOR 段。
- **视频**：`msg/video/<YYYY-MM>/<md5>.mp4` 或 `msg/file/<YYYY-MM>/...`，明文。
- **文件**：`msg/file/<YYYY-MM>/<原文件名>` 或 `attach/.../File/...`，明文；由
  `file_hardlink_info_v4` 的 md5 定位。
- **语音**：优先从 `message/media_0.db` 的 `VoiceInfo.voice_data` 取 SILK 字节；
  没有就按 `voicemd5` 找缓存文件。默认同时保留 `.silk` 并转 `.wav`（需
  `pip install pilk`）；没装 `pilk` 时只导出 `.silk`，HTML 里给下载链接。
  `--voice-format both|wav|silk` 控制输出。
- **表情**：先找本地缓存；没有时只有显式 `--download-stickers` 才会从消息里的
  `cdnurl`/`externurl` 尝试下载（`encrypturl` 会尝试用 `aeskey` 解密）。
- **公众号正文**：只有显式 `--fetch-articles` 才会联网抓取 `mp.weixin.qq.com`
  文章，保存 `.txt` + `.html`；默认只保留标题/描述/URL。
- **合并转发**：解析 `recorditem`，HTML/JSON/MD 中展开内层每条的文字、发送者、
  时间、文件名；内层文件若在本机缓存则复制到 `media/forward/`，
  内层图片/视频无本地缓存时标记 `missing`（不走 CDN 协议）。
- **引用消息**：解析 `refermsg`，还原被引用消息的发送者、时间、原文/应用消息标题；
  被引用的图片/视频本体不额外下载。
- **WXGF**：`_W.dat`/`wxgf` 若是单字节 XOR 的 PNG/JPEG/BMP，会自动还原；
  真正的 HEVC 内层仍需额外解码器。
- 本机未下载/已清理的媒体默认无法恢复，标记 `missing`；只有上述两个显式开关
  才会联网补数据。

## 图片密钥（唯一的硬门槛）

文字、视频、文件不需要图片密钥。**图片解密需要**：

- `image_aes`：16 位 ASCII，微信账号级密钥。优先从 `keys.json` 读；否则在账号
  已登录时从 `weixin.dll` 的 cfg 对象只读取出 `cfgDword`，按
  `md5(str(cfgDword)+wxid)[:16]` 现场派生（不打印、不落盘）。
- `image_xor`：1 字节，可由 WeFlow 配置的 `imageXorKey`（safeStorage）读取，
  或由 `cfgDword & 0xFF` 得到，或从 `_t.dat` 缩略图尾部反推。

写到 `keys.json`（可选，用于离线复用）：

```json
{"image_aes": "16位ASCII", "image_xor": 123, "cfg_dword": 0}
```

没有 `image_aes` 时，已存在于微信解码缓存的图片仍可导出；其余只有加密 `.dat`
的图片会标记 `locked`。账号已登录时通常会现场派生成功，无需手工填 key。

## 其它命令

```bash
python wfchat.py decrypt-image <某.dat> --out <目录> [--xor N] [--aes 16位]
python lib/keyscan.py <账号目录>   # 仅诊断：只输出 FOUND/NOT FOUND
```

`decrypt-image` 与 `export` 都使用纯 Python 的 AES/XOR 解密，不依赖 Node、原生插件或第三方导出工具。

## 已知边界

- 只读：解密产物和导出物只写到 `$WECHAT_WORK_DIR`（默认 `~/.wechat-chatlog/work`）或 `--out`，不触碰 `xwechat_files`。
- 工作目录里会留下**解密后的明文数据库副本**（大账号可达 GB 级）；用
  `python wfchat.py clean` 清除。
- 全程不使用截图/UI 抓取；密钥兜底只做只读进程内存读取（不注入、不 hook、不 patch）。
- 没有存储密钥且微信未登录该账号时，无法自动取 key，`doctor` 报 `key: missing`。
- 群聊发送者按 `real_sender_id` + `Name2Id` 还原；`@所有人` 等归为 `system`。
- 朋友圈、小程序、视频号内层内容不展开（保留标题/链接/占位）。
- 合并转发会展开内层文字/文件名/发送者/时间；内层图片/视频不额外下载。
- 表情优先本地缓存；无缓存时需显式 `--download-stickers` 才会联网下载。
- 公众号正文默认不抓取；需显式 `--fetch-articles`，且只处理 `mp.weixin.qq.com`。
- 语音默认导出 SILK 并在装了 `pilk` 时转 WAV；不转写。未装 `pilk` 时只有 `.silk`。
- `--all` 会遍历全部会话，账号很大时耗时按会话数增长（每个会话约 0.5–1s）；
  建议配合 `--limit-sessions` 和 `--since`。
- 导出内容含隐私，默认只落本地；不要提交或外传。
- 微信升级（如 4.3/4.4）后先跑 `diagnose`；版本适配清单见
  `VERSION_MAINTENANCE.md`，参考项目清单见 `REFERENCES.md`，待办清单见 `TODO.md`。
