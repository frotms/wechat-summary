# 微信版本升级维护手册（4.x → 4.3/4.4）

目标：微信升级后，能快速判断“哪里变了”，并在尽量少的改动内恢复导出。
原则：**每一层都有可验证的探针和降级路径；失败要显式报错，不能静默导出错数据。**

## 0. 依赖边界（重要）

本 skill 已经**自包含**：

- 引擎代码全部在 `.claude/skills/wechat-chatlog/lib/`：
  `v4db.py`（SQLCipher）、`wxdata.py`（DB/消息/媒体索引）、`keyscan.py`（只读 key 扫描）。
- 不 import、不调用 `wechat-exporter`、`wechat-cli`、WeFlow 的可执行文件或 DLL。
- 图片/视频/文件解密全部是纯 Python，不依赖 Node、原生 addon。
- 可选依赖只有通用库：
  - 必需：Python 3.10+、`cryptography`、`zstandard`
  - 可选：`sqlcipher3`（用于直接打开带 WAL 的副本；缺失时退回纯 Python 主库解密）
  - 可选：`pilk`（SILK 语音转 WAV；GPL-3.0，本地使用；缺失时只导出 `.silk`）
  - 可选：Tencent [WCDB](https://github.com/Tencent/wcdb)（仅作为 C++ 侧兼容性校验/备选后端，
    不是运行必需）
- WeFlow 配置只作为**可选的旧账号 key 来源**，不是依赖；没有它也能用
  `keys.json` / `--key-file` / 只读内存扫描。

因此微信 4.3/4.4 升级时，更新点在 `lib/` 和官方 WCDB 语义内，不需要等第三方导出工具发版。

参考项目（仅参考、不作为依赖）已记录在 `REFERENCES.md`：WeChatExporter、weflow-cli、
WeFlow、Moect、WeChatDataAnalysis、wechat-chat-export、wechat-decrypt、
wechat-dump-rs、wechatauto-replica 等；升级时先看它们的同版本 issue/release 作为假设，
再用本 skill 的 `diagnose` 和 HMAC/导出结果验证。

## 1. 哪些地方会随版本变化

| 层 | 当前实现 | 版本敏感点 | 升级后可能的变化 |
|---|---|---|---|
| 数据库加密 | `lib/v4db.py`：4096 页、PBKDF2-HMAC-SHA512 256000、页尾 80B（16 IV + 64 HMAC）、AES-256-CBC | 页大小、KDF 算法/迭代、HMAC 算法、保留区大小、WAL 格式 | SQLCipher 4.x 小版本通常不变；大版本可能改参数或启用明文头 |
| 数据库密钥 | `lib/keyscan.py`：`com.Tencent.WCDB.Config.Cipher` 配置对象 + XOR 掩码 + 每库 HMAC 校验 | 配置字符串、XOR 掩码、对象偏移、key 存储形态 | 字符串/掩码/偏移变化；key 改为新配置对象或更强保护 |
| 图片 key | `cfgDword`：`md5(str(cfgDword)+wxid)[:16]`，`xor=cfgDword&0xFF` | `weixin.dll` 的 `global_config` 地标、指针链偏移、cfgDword 偏移、派生公式 | 偏移变化；公式可能改为别的派生 |
| DB schema | `lib/wxdata.py`：`SessionTable`、`Msg_<md5(username)>`、`Name2Id`、`message_content`、`WCDB_CT_message_content`、`real_sender_id` 等 | 表名/列名/分片规则/压缩方式/消息类型编码 | 新增分片、列重命名、内容容器/压缩变化 |
| 媒体布局 | `wxexport.py`：`msg/attach/.../Img`、`msg/video`、`msg/file`、`hardlink.db`、`message_resource.db`、`cache/.../ImageTemp` | 目录、hardlink 表名/列、资源表 packed_info、图片格式（V2/V3/WXGF） | 路径变化、新增资源表、图片加密升级 |

## 2. 升级后第一步：诊断

```bash
python wfchat.py --account <账号目录> diagnose
```

输出（不含密钥）：

- 微信版本、`Weixin.dll` 的 SHA-256/大小；
- 密钥方式（`passphrase` / `raw-scan`）、可用 DB 数量；
- 关键 DB 的表数量与 schema 指纹（`sha256` 前 16 位）；
- 媒体目录文件数、WAL 字节数；
- 图片 key 方式（`cfgDword` / `none`）；
- `warnings`：未知版本、schema/媒体异常。

**当前基线（2026-11-17，微信 4.1.13.7）**：

```
session/session.db                tables=7   sha256=45f756424845ca10
contact/contact.db                tables=16  sha256=e5f0bb08ca56f3c1
hardlink/hardlink.db              tables=8   sha256=d04ee8ed7eb7fd78
message/message_0.db              tables=6   sha256=b0959be38d7ce915
message/message_resource.db       tables=7   sha256=a8951c584268eab8
Weixin.dll sha256=f4c4118d85ee743a0832c301288edf77b041e02f84b73d7905ffe10090206d01
```

升级后先对比这张表：

- 版本/`dll_sha256` 变了但 schema 指纹不变 → 大概率只需重新定位 key 锚点。
- schema 指纹变了 → 需要更新 `lib/wxdata.py` 的字段/表适配。
- 媒体数量骤降/图片 key `none` → 检查媒体布局或图片 key 派生。

## 3. 分层更新方法

### 3.1 数据库参数变了

优先用环境变量试参数，不改代码：

```bash
WECHAT_PAGE_SIZE=4096 WECHAT_KDF_ITER=256000 WECHAT_RESERVE=80 \
WECHAT_HMAC_SIZE=64 WECHAT_SALT_SIZE=16 python wfchat.py diagnose
```

如果 SQLCipher 换算法（例如 HMAC-SHA256、不同 KDF），需要改
`v4db.derive_keys/_page_mac`，并用一个已知 key 的测试库做 HMAC/AES 校验。

### 3.2 密钥锚点变了

`lib/keyscan.py` 的定位顺序：

1. `keys.json` / WeFlow 配置 / `--key-file`；
2. `com.Tencent.WCDB.Config.Cipher` + XOR 掩码；
3. 对每个候选做 SQLCipher HMAC 校验（**校验是最终判据**，不靠偏移猜）。

快速调整（不改代码）：

```bash
# 新的 global_config 指针链/cfgDword 偏移
WECHAT_CFG_BACK=0x138 WECHAT_CFG_OFFSET=0x68 WECHAT_CFG_DWORD_OFF=0x40 \
python lib/keyscan.py <账号目录>

# 新的 Config.Cipher XOR 掩码（hex）
WECHAT_CONFIG_XOR_MASK=<新掩码hex> python lib/keyscan.py <账号目录>
```

如果字符串/对象布局完全变了：

1. 在 `Weixin.dll` 里重新找 `global_config`、`Config.Cipher` 或新的配置字符串；
2. 用 `VirtualQueryEx + ReadProcessMemory` dump 候选结构（只读）；
3. 用“候选 + 每库 HMAC 校验”确认，不依赖单一偏移；
4. 把新偏移/掩码写入 `lib/keyscan.py` 的版本 profile 或环境变量；
5. 保留旧版本的常量，按 `weixin_info()["version"]` 选择。

### 3.3 DB schema 变了

`diagnose` 的 schema 指纹会变化。处理顺序：

1. 打开对应库，`select name, sql from sqlite_master` 对比表/列；
2. 更新 `lib/wxdata.py` 里的表名/列名映射（建议抽成 `schema_v4.py` 适配层）；
3. 如果消息内容换了容器/压缩，更新 `decompress()` 和 `parse_content()`；
4. 用 `messages --last N` 与微信界面抽样对比，确认发送者/时间/类型正确；
5. 加一个合成测试库，锁定新 schema 的解析结果。

### 3.4 图片/视频/文件布局变了

1. 用 `diagnose` 看 `msg/attach`、`msg/video`、`msg/file`、`cache/ImageTemp` 数量；
2. 找新路径/新索引表（`hardlink.db`、`message_resource.db` 或新表）；
3. 图片先尝试明文解码缓存（`cache/.../ImageTemp`、`Thumb`），再尝试 `.dat`；
4. 新图片格式（如 V3）在 `wxexport.decrypt_dat` 加分支，失败时保存原始字节并标记
   `unsupported`，绝不输出坏图；
5. 视频/文件若变成加密，先找 key，再决定是否实现解密；不要只按扩展名猜。

## 4. 回归与验收

每次升级后至少跑：

```bash
python wfchat.py --account <账号> diagnose
python wfchat.py --account <账号> sessions --limit 10
python wfchat.py --account <账号> messages --session "<测试会话>" --last 20
python wfchat.py --account <账号> export --session "<测试会话>" --last 50 \
  --types text,image,file,video --format html,json
```

验收标准：

- `doctor`/`diagnose` 的 key 方式不是 `missing`；
- 抽样的 10 条消息内容/发送者/时间与微信一致；
- 导出的图片能打开（JPEG/PNG 魔数正确），文件大小与源一致；
- HTML 中图片/视频/文件链接可打开；
- `manifest.json` 的 `missing/locked/unsupported` 数量合理，且原因明确；
- 不打印、不落盘任何密钥。

## 5. 怎么“保证”能做更新

不能保证腾讯不改变加密或采取法律/风控措施；能保证的是：

1. **可检测**：版本、DLL 哈希、schema 指纹、媒体数量、key 方式都有探针；
2. **可降级**：存储 key → 配置对象 → 通用候选扫描 → 外部 key；图片明文缓存 →
   `.dat` 解密 → 原始字节；读取失败不会静默产出错误数据；
3. **可替换**：偏移/掩码/参数支持环境变量覆盖；schema/媒体解析按层拆分，新增
   `v43/v44` 适配即可，不必重写整个 skill；
4. **可验证**：每个候选 key 都过 SQLCipher HMAC；每次导出都有 manifest；
5. **有基线**：当前版本的 schema/DLL 指纹已记录，升级后一跑 `diagnose` 就知道差异。

预计工作量：

- 仅偏移/掩码变化：约 0.5–2 小时（改环境变量或 profile + 跑验收）。
- 配置对象/key 形态变化：约 1 天（重新定位锚点 + 多候选校验）。
- schema/媒体格式变化：约 1–3 天（适配解析 + 回归）。
- 加密算法/密钥保护彻底重做：不确定，需要重新逆向，且可能无法保证成功。

## 6. 升级时的操作顺序

1. 先跑 `diagnose`，保存输出；
2. 不要直接批量导出，先用一个会话 `--last 20` 验证；
3. 如果 key 失败，先用环境变量试新偏移/掩码；
4. 如果 schema 变化，先修解析，再跑媒体；
5. 通过验收后再批量导出；
6. 把新版本指纹和适配改动记录到本文件。
