# 输出 schema（wechat-export）

本文件定义导出产物的稳定字段。版本号写在各自的 `schema` 字段里；新增字段保持向后兼容。

## 1. `messages.jsonl`

一行一条消息，UTF-8 JSON。`bundle.manifest.json` 的 `messageSchema` 为
`wechat-export-message/v1`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | `localId:createTime`，会话内稳定 |
| `seq` | number | 按时间排序后的序号 |
| `localId` / `serverId` | number/string | 微信消息身份；`audit --deep` 使用 |
| `timestamp` | number | Unix 秒 |
| `iso` / `localTime` / `day` | string | ISO、本地时间、日期 |
| `senderId` | string | wxid/@chatroom |
| `sender` | string | 显示名（备注/昵称/“我”） |
| `isMe` | boolean | 是否本人发送 |
| `type` | string | `text/image/file/video/voice/emoji/link/miniprogram/history/transfer/redpacket/location/card/call/system/quote/other` |
| `typeLabel` | string | 中文类型名 |
| `messageType` | string | 原生解析的 kind |
| `rawType` | number | 低 32 位 local_type |
| `text` | string | 展示文本；文件/特殊消息为摘要 |
| `media` | object | `image/file/video/voice/sticker/poster` 相对路径 |
| `mediaStatus` | string | `exported` / `partial` / `missing` / `none` |
| `missing` | array | `[{kind, reason}]`；本机没有的媒体 |
| `missingReason` | string? | 第一个缺失原因（便于脚本消费） |
| `quote` | object? | `{sender, text, kind}`，引用消息 |
| `reply` | string? | 引用消息的回复正文 |
| `records` | array? | 合并转发内层：`{kind, sender, time, text, title, size, md5, media?, mediaStatus?, missingReason?}` |
| `miniprogram` | object? | `{title, description, appName, appId, pagePath, username, iconUrl, url, appType}` |
| `isNote` | boolean? | 合并转发为“笔记” |
| `amount` | string? | 转账金额 |
| `redpacket` | object? | `{title, description}` |
| `location` | object? | `{lat, lng, title, city, url}` |
| `contact` | object? | `{username, title}`（名片） |
| `revoked` | boolean? | 已撤回 |
| `revoke` | object? | 撤回元数据 |
| `rawContent` | string? | 仅 `--include-raw` |

## 2. `manifest.json`（会话）

- `schema`: `wechat-export-bundle/v1`
- `messageSchema`: `wechat-export-message/v1`
- `target`, `query`, `stats`, `media`, `warnings`、`source.mediaExtraction`、`source.revokes`
- `redaction`: `{mode}`（启用 `--redact` 时）
- `incremental`: 增量计数（启用 `--incremental` 时）

## 3. 根 `manifest.json`

- `schema`: `wechat-export/bundle-index/v1`
- `bundles`: 会话目录、目标、统计；`query` 为本次导出的全局条件。
- `retention`: `{days, deleteAfter}`（启用 `--retention-days` 时）
- `encrypted`: `{file, files, removedPlaintext}`（启用 `--encrypt` 时）

## 4. `audit.json`

- `schema`: `wechat-export-audit/v1`
- `totals`: 数据库/原始/导出/跳过/缺失媒体/重复/时间窗外
- `targets[]`: 每个会话的 `source`、`export`、`checks`、`deep`、`complete`
- `deep`: `--deep` 时的 `missingIds` / `extraIds` / `exportedNotInRaw` / `typeMismatch`
  - `deep.boundary.source`: `raw-coverage` / `raw-max` / `generatedAt`
  - `deep.boundary.reliable`: 旧导出无 `coverage.boundary` 时为 false


## 5. `state.json` / `incremental.json`

仅 `--incremental` 的会话目录生成。

`state.json`（`wechat-export-state/v1`）：

- `target`: `{id, name, kind}`
- `lastTimestamp`: 已导出消息的最大 Unix 秒
- `lastMessageId`: 最大时间戳对应的消息 `id`
- `messageCount`: `messages.jsonl` 的总条数
- `updatedAt`: ISO 时间

`incremental.json`（`wechat-export-incremental/v1`）：

- `fromDate`: 本次增量实际使用的起始日期
- `previousLastTimestamp` / `previousMessages`: 运行前状态
- `fetchedMessages`: 本次从源库取回并解析的原始消息数
- `parsedRecords`: 按 `--types` 过滤后的记录数
- `newMessages` / `duplicateMessages`: 新增 / 已存在而跳过
- `totalMessages`: 合并后的 `messages.jsonl` 条数
- `firstTimestamp` / `lastTimestamp`: 合并后的时间范围
- `check`: 导出时自检结果（`ok`、重复 id、缺失媒体、重复媒体）

独立复验：

```bash
node scripts/wechat-incremental-check.mjs --output <导出目录> [--json]
# 或
node scripts/wechat42-export.mjs check-incremental --output <导出目录>
```

检查 `state.json` 与 JSONL 一致、计数闭合、无重复 id、按时间排序、
媒体文件存在且同一 sha256 没有重复落盘。

## 6. `revokes.json`

- 每个会话一份：`items`（匹配到的撤回）、`orphans`（无法匹配的记录）。

## 7. `contacts.json` / `groups.json`

`node scripts/wechat42-export.mjs contacts --output <目录>`

### contacts.json

- `schema`: `wechat-export-contacts/v1`
- `counts`: `{contacts, groups, avatars}`
- `contacts[]`: `username`, `displayName`, `remark`, `nickname`, `alias`, `type`
  (`person/group/official/openim`), `localType`, `description`, `deleted`,
  `headImageMd5`, `headImageUrl`, `headImage`（相对路径）

### groups.json

- `schema`: `wechat-export-groups/v1`
- `groups[]`: `username`, `displayName`, `owner`, `memberCount`, `members[]`
  - `members[]`: `username`, `displayName`, `remark`, `nickname`, `alias`

## 7. 稳定性约定

- 字段新增是兼容的；重命名/删除会提升 schema 版本。
- `text` 是展示摘要，不保证等于原始 XML；需要原始内容用 `--include-raw`。
- 媒体路径相对于会话目录；`missing` 不代表可以联网恢复。
- `contacts/groups` 只读 `work/` 的解密库，不上传。

## 8. 加密包 `.wxenc`

`--encrypt` 生成单个 AES-256-GCM 文件：

- 密码来源：环境变量 `WECHAT_EXPORT_PASSWORD`，不写入命令行/文件
- KDF：scrypt；每个包独立 salt/nonce
- 包内路径、大小和文件内容一起加密；认证标签防篡改
- 解密：`node scripts/wechat42-export.mjs decrypt-output --input <包> --output <目录>`
- 默认保留明文目录；只有显式 `--remove-plaintext` 才删除

## 9. 保留期

`--retention-days <n>` 在根 `manifest.json` 写 `retention.deleteAfter`。
`retention-clean --output <根目录>` 默认只列出到期目录，加 `--yes` 才删除。
