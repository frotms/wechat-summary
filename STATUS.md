# 当前状态与待办

> 版本：1.13.0（2026-11-24）
> 分支：仅 `main`（feat 已于 2026-11-20 收口并删除；旧 main 见 tag `archive-main-python-4.1-2026-11-18`）
> 验证环境：Windows 10 + 微信桌面版 4.2.0.13
> 详细设计见 `DESIGN.md`；按优先级排列的待办见 `TODO.md`；已记录但暂不实现的需求见 `BACKLOG.md`。

## 一、当前功能

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 文字消息 | ✅ | `messages.jsonl` / `messages.json` / `transcript.md` / HTML |
| 图片 | ✅ | `media/images/`，缺失写 `missing.reason` |
| 传输文件 | ✅ | `media/files/`，保留原文件名与大小 |
| 视频 | ✅ | `msg/video` 按 md5 匹配，`media/videos/`，含封面 |
| 语音 | ✅ | 4.2 自动 SILK→WAV；4.1 用 `wechat41-export.mjs` |
| 表情 | ✅ | 内置表情码渲染为 emoji；自定义表情包解密为 PNG/GIF |
| 文章链接 / 公众号 | ✅ | `link` 类型 + `appUrl/appTitle` fallback；`--all-official` |
| 撤回记录 | ✅ | 批量撤回匹配原消息；单条/孤立记录写 `revokes.json`，消息标记 `revoked` |
| 引用消息 | ✅ | `refermsg` 还原发送者/原文为 `quote`；HTML 引用块、Markdown 引用 |
| 合并转发/笔记 | ✅ | `recorditem` 展开内层文字/文件名/发送者/时间为 `records`；HTML `<details>`、Markdown 引用块 |
| 转账/红包 | ✅ | 金额/标题/说明进入 `amount`/`redpacket`；HTML 卡片、JSONL 可直接消费 |
| 位置 | ✅ | 经纬度/地点/城市/高德链接进入 `location`；HTML 地图卡、Markdown 链接 |
| 名片 | ✅ | 昵称/wxid 进入 `contact`；HTML 卡片、JSONL 可直接消费 |
| 按时间/群/个人 | ✅ | `--from/--to/--date/--days`、`--group/--person`、`--all-*` |
| V2 加密视频 | ✅ | `lib/v2_decode.py`，4.2 媒体映射自动尝试 |
| 完整性校验 | ✅ | 数据库行数 = 原始 JSON = 导出 + 按类型跳过；`audit.json`；`audit --deep` 按 `local_id+server_id` 逐条对账 |
| 只读硬约束 | ✅ | weflow-cli 命令白名单 + 输出路径保护；不写/不转发/不增删改 |
| 4.2 后端 | ✅ | 内置 vendor（wechat-exporter MIT）+ sandbox；默认 `native-backend.py` 读取 `work/`，4.2 导出不再依赖 `weflow-cli`（`--legacy-weflow` 可回退） |
| 4.2 key 缓存 | ✅ | `setup` 保存 `<baseDir>/keys.json`；后续优先缓存，无需微信进程；`clean --purge-keys` 删除 |
| native 媒体 | ✅ | V2 视频解码 + 封面复制；types 含 image/file 时跳过冗余 media-map；`--with-media-map` 可强制 |
| native 缓存 | ✅ | 文件索引 TTL 600s + `--refresh` 强制刷新；视频索引可刷新；表情 seed 样本校验/重探 |
| 联系人/群/头像 | ✅ | `contacts` → `contacts.json` / `groups.json` / `avatars/`；真实数据 10309/602/3020 |
| 公众号正文 | ✅ 可选 | `--fetch-articles`，仅 `mp.weixin.qq.com`，默认关闭；真实 canary 已抓到 Qwen4 正文 |
| 表情 CDN | ✅ 可选 | `--download-stickers`，Tencent 白名单，默认关闭；合成测试通过 |
| 跨会话/时间线 | ✅ | `person --sender`、`timeline`；`--incremental` 基于 `state.json` |
| 按会话增量 | ✅ | `--target/--group/--person --incremental` 追加新消息；媒体 sha256 去重；`check-incremental` 复验 |
| 小程序 | ✅ | appid/pagepath/username/icon 等进入 `miniprogram`；HTML/Markdown 卡片 |
| 合并转发内层媒体 | ✅ 本机缓存 | 图片/视频/文件优先本机定位；拿不到标记 `forward-media-not-local` |
| V2 图片 / WXGF | ✅ | V2 图片解密；WXGF/H.265 转 JPEG；真实 V2 图片 canary 通过 |
| 脱敏/加密/保留期 | ✅ | `--redact basic|strict`、`--encrypt`、`--retention-days`；`decrypt-output` / `retention-clean` |
| 图片单文件解密 | ✅ | `decrypt-image`；V2 + XOR 合成测试 |
| 输出 schema | ✅ | `mediaStatus`/`missingReason`；`docs/OUTPUT_SCHEMA.md` |
| Skill 安装 | ✅ | `~/.claude/skills/wechat-export` junction；当前会话已识别；安装路径 `--check` 通过 |
| 4.1 后端 | ⏸ 暂缓 | 代码保留，但暂不维护/未真机验证；旧实现归档在 tag `archive-main-python-4.1-2026-11-18` |
| 脱离 `weflow-cli` | ✅ 4.2 原生 | 4.2 默认 `native-backend.py` + `native-weflow.mjs`；4.1 代码保留但不维护；`--legacy-weflow` 保留回退 |
| 环境体检 | ✅ | `--check`、`wechat42-export.mjs doctor` |
| 测试 | ✅ | 81 项 Node + 62 项 Python |

### 本机验证基线（微信 4.2.0.13）

- 2026-10-26：61 条，文字 39、图片 18（找到 13）、文件 4（找到 4）
- 2026-11-03：33 条，文字 25、图片 6（找到 6）、文件 1、视频 1
- 2026-11-13：SEx 群 47 条，语音 4（找到 4，WAV）
- 2026-11-02：表情 21 条，18 条解密成功（PNG 3 / GIF 15）
- 2026-11-17：批量撤回 2 条，2/2 匹配原消息
- 2026-10-29：完整性校验 557 = 557 = 557；`--types text` 时 441 + 跳过 116 = 557
- 2026-11-18：Level 2 原生 4.2 实测：文字 9/9、图片 2/2、文件 3/3、语音 4/4、视频 1/1；audit 通过
- 2026-11-19：`audit --deep` 逐条 ID 对账：完整链 0 missing / 0 extra；一丢一重、尾部缺失、导出后新消息边界、类型不符均有测试
- 2026-11-19：引用/合并转发：`refermsg` 还原为 `quote`，`recorditem` 展开为 `records`；HTML/Markdown/JSONL 均有测试
- 2026-11-19：真实 4.2 canary：普曼群 84 条引用；理财群 91 条引用 + 6 条合并转发；HTML 引用块 84/91、`<details>` 6 个，audit 通过
- 2026-11-19：clean 后带 `--no-media-map --no-revoke-map --no-voice-map --no-sticker-map` 导出：自动重建 work/，47 条，audit 通过
- 2026-11-20：文本型导出跳过媒体/表情映射（真实 4.2 canary：无 media-map/sticker-seed 日志，audit 通过）
- 2026-11-20：特殊消息真实 canary：9 位置 / 2 名片 / 1 红包 / 2 转账；JSONL/HTML/Markdown 通过，audit 3/3
- 2026-11-20：真实 4.2 `audit --deep`：源库 275 = 原始 275，导出 235 + 跳过 40；missing/extra/exportedNotInRaw 均为 0，boundary reliable=true
- 2026-11-20：4.2 key 缓存：setup 保存 21 个 raw key；clean（删除 work）后 setup 使用 `source=cache`，21/21 解密，无需重新提取
- 2026-11-20：真实媒体 canary（frotms 小号版，12 条）：图片 2/2、语音 2/2 导出；视频 1 条本机未缓存标记 `video-not-local`；native 跳过 media-map，`audit --deep` 0 missing/extra
- 2026-11-23：Skill 安装验证：junction 到 `~/.claude/skills/wechat-export`，当前会话识别 `wechat-export`；安装路径 `--check` 使用 key 缓存通过
- 2026-11-23：联系人 canary：10309 联系人 / 602 群 / 3020 头像
- 2026-11-23：公众号正文 canary：官方群 link/text 消息抓取到 Qwen4 正文，写入 `media/articles/*.txt/.html`；HTML 正文卡与本地 html 路径验证通过
- 2026-11-23：`decrypt-image` 真实 canary：从 account 派生 key，输出 PNG 39839 bytes
- 2026-11-23：person/timeline canary：person 38 条（50 会话）；timeline 200 条；`--incremental` 保留已有行并只追加新消息
- 2026-11-24：按会话增量 canary：国数SE9供货沟通 首次 4 条，第二次新增 35、重复 4、总计 39；`check-incremental` 0 失败
- 2026-11-24：真实 V2 图片 canary：`631878c3...dat` → PNG 11451 bytes；本机 1370 个视频无 V2，176592 个图片缓存无 WXGF 样本

## 二、已知限制

1. **普通单条撤回**：本地通常只剩 `sysmsg type="revokemsg"` 提示，没有 `newmsgid`，原内容一般拿不到；`revokemessage` 表本机 0 行，未验证。
2. **批量撤回**：依赖原消息行仍在；原行被删除则无法恢复。
3. **`weflow-cli` 依赖**：4.2 默认走原生后端，不再需要 `weflow-cli`；只有 `--legacy-weflow` 回退时才用。4.1 代码保留但暂不维护。
3. **实时库一致性**：普通计数 `audit` 现在优先用 `coverage.boundary`，并扣除同秒内晚于导出边界的尾部行；旧导出没有边界时仍可能误报。`audit --deep` 继续按 ID 逐条对账。
4. **4.1 暂缓**：用户 2026-11-20 确认先不管 4.1；相关代码、合成测试保留，但不维护/不验收。旧实现见 tag `archive-main-python-4.1-2026-11-18`。
5. **V2/HEVC 图片**：V2 图片解密已用真实样本验证；WXGF/H.265 走 vendor `prepare_wxam`，本机 176592 个图片缓存无 WXGF 样本，未做真实 canary。
6. **真实 V2 视频**：本机 1370 个视频全是普通 MP4，V2 只有合成测试。
7. **非文本消息结构化**：小程序已展开 title/des/appid/pagepath/username/icon；视频号、部分应用消息仍未展开；转账/红包/位置/名片已结构化（领取/退款等状态字段未解析）。
8. **公众号**：本机无 `gh_` 会话，`--all-official` 只有假 CLI 测试。
9. **大会话性能**：native 全媒体类型统一走 `--media`，默认不生成 media-map；仅 legacy / `--with-media-map` 才扫描。
10. **跨平台**：只在 Windows 10/11 + 微信 4.x 验证；macOS/Linux、微信 3.x 未验证。
11. **输出安全**：默认仍是明文目录；`--redact` 做基础脱敏、`--encrypt` 生成 AES-256-GCM 加密包、`--retention-days` 写保留期。加密密码只从 `WECHAT_EXPORT_PASSWORD` 读取。
12. **依赖安装**：已提供 `requirements.txt`；仍需用户自己创建 venv 并安装（当前不做一键安装器/安装向导）。
13. **联网能力**：`--fetch-articles` 只支持 `mp.weixin.qq.com`，`--download-stickers` 只支持 Tencent 白名单；默认关闭，不绕过登录/风控。
14. **增量范围**：`--incremental` 已覆盖普通按会话导出和 `person`/`timeline`；增量导出要求固定 `--output`，删除/移动旧导出目录后需要重新全量导出。
15. **表情 CDN**：只有合成测试，尚未用真实“本机缺失表情”做 canary。

## 三、待办（按优先级）

### P0：结果可信度

- [x] 实时库一致性：普通计数 audit 也用 `coverage.boundary` 并扣除同秒尾部行；`audit --deep` 继续逐条对账。
- [ ] 单条撤回 `revokemessage` 实样：确认 `content` 是否为原文、`svr_id` 格式、`revoke_time` 单位。
- [x] 大会话性能：native 全媒体类型统一走 `--media`，默认不再生成 media-map；仅 legacy/`--with-media-map` 保留。

### P1：覆盖范围

- [x] V2 图片解密（真实 canary）；WXGF/H.265 转码已接入，本机无真实 WXGF 样本。
- [ ] 真实 V2 视频 canary。
- [x] 红包/转账/位置/名片结构化（金额/标题/经纬度/wxid）；小程序 appid/pagepath/username/icon 已补。
- [ ] 真实 `gh_` 公众号 canary。

### P2：工程化

- [x] 根 LICENSE 统一为 MIT（2026-11-18）。
- [x] 清理误生成的仓库内空目录（2026-11-18）。
- [x] **Level 2（4.2）**：`native-backend.py` + `native-weflow.mjs`，4.2 导出不再调用 `weflow-cli`；`--legacy-weflow` 保留对照。
- [x] **Level 2（4.1）**：`wechat41-decrypt.py` + `lib/v41db.py`，SQLCipher → `work/` → native backend；合成 SQLCipher 测试通过，真机待验证。
- [x] **native-only setup**：4.2 `setup` 默认只生成 `work/`，`--with-sandbox` 才生成 sandbox。
- [x] 推送并跑 GitHub Actions（2026-11-24：Node 81 / Python 62 通过；时区测试已修）。
- [ ] 依赖清单与一键安装；4.1 版 `doctor` 暂缓。
- [ ] 跨平台验证（macOS/Linux、微信 3.x）。
- [x] 导出物脱敏/加密/保留期：`--redact`、`--encrypt`、`--retention-days`；`decrypt-output`/`retention-clean`。
- [x] 更新 `SKILL.md` frontmatter description，覆盖语音、表情、撤回、引用、合并转发、完整性校验。

### 已记录、暂不实现（见 `BACKLOG.md`）

- 微信收藏（REQ-014）
- 朋友圈
- 公众号文章正文抓取
- 语音转写（REQ-016b）
- 运行时完全脱离 `weflow-cli`（Level 2）

## 四、如何自检

```bash
npm test                  # 81 项 Node 测试
npm run test:py           # 62 项 Python 测试

node scripts/wechat42-export.mjs doctor
node scripts/wechat42-export.mjs audit --output "<导出目录>" --deep
```

## 五、许可证与第三方

- 本仓库：MIT（根 `LICENSE`）。
- `scripts/vendor/wechat42/`：来自 MIT 项目 `TangGod991021/wechat-exporter`，见目录内 `LICENSE` / `NOTICE.md`。
- 读取底座 `weflow-cli`：MIT（仅 `--legacy-weflow` 可选）。
- `WeFlow`（`hicccc77/WeFlow`）：CC BY-NC-SA 4.0，仅作设计参考，未 vendor。
- 完整第三方清单、文档来源和分发注意见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 与 [`NOTICE`](NOTICE)。
