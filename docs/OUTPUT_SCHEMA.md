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
| `type` | string | `text/image/file/video/voice/emoji/link/history/transfer/redpacket/location/card/call/system/quote/other` |
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
| `records` | array? | 合并转发内层：`{kind, sender, time, text, title, size, md5...}` |
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

## 3. 根 `manifest.json`

- `schema`: `wechat-export/bundle-index/v1`
- `bundles`: 会话目录、目标、统计；`query` 为本次导出的全局条件。

## 4. `audit.json`

- `schema`: `wechat-export-audit/v1`
- `totals`: 数据库/原始/导出/跳过/缺失媒体/重复/时间窗外
- `targets[]`: 每个会话的 `source`、`export`、`checks`、`deep`、`complete`
- `deep`: `--deep` 时的 `missingIds` / `extraIds` / `exportedNotInRaw` / `typeMismatch`
  - `deep.boundary.source`: `raw-coverage` / `raw-max` / `generatedAt`
  - `deep.boundary.reliable`: 旧导出无 `coverage.boundary` 时为 false

## 5. `revokes.json`

- 每个会话一份：`items`（匹配到的撤回）、`orphans`（无法匹配的记录）。

## 6. `contacts.json` / `groups.json`

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
