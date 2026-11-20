# 分支收敛计划：main → feat，最终一个主干

> 建立：2026-11-19  
> feat 基线：`80c7573`（tag `baseline-feat-2026-11-19`）  
> main 归档：`9582320`（tag `archive-main-python-4.1-2026-11-18`）  
> 基线测试：47 项 Node + 29 项 Python 全绿（Phase 0）；完成第 1/3/4 项与 P0 收口后为 52 + 41

## 0. 目标

最终只保留：

- **一个活跃分支**：`main`
- **一个 Skill**：`wechat-export`
- **一个用户入口**：`scripts/wechat-export.mjs`（内部再分发 4.1/4.2）
- **一套输出契约**：`messages.jsonl` + `manifest.json` + `audit.json` + `revokes.json`
- **一套测试**：Node + Python，CI 全绿
- **一份文档真相**：README / SKILL / STATUS / TODO / BACKLOG / DESIGN

main 不是被“合并进来”，而是作为**能力库**：只移植其中 feat 没有或更弱的能力。

## 1. 明确不做

- 不直接 `git merge main`：两套实现、根文件几乎全冲突，解完仍是两套入口
- 不保留两套 4.1 引擎、两套消息 parser、两份 TODO/STATUS
- 不把 main 的 Apache-2.0 代码无声明混入；移植文件保留来源与 NOTICE
- 不为兼容保留两套输出格式；需要旧格式时写一次性转换器

## 2. 固定决策

| 决策 | 结论 | 说明 |
| --- | --- | --- |
| 唯一主干 | feat | 4.2 真机验证、76 项测试、CI、4.1/4.2 原生都在 feat |
| 运行时 | Node + Python | Node 只做编排、打包、HTML/Markdown；不重写为纯 Python |
| 解析层 | Python 单点 | 4.1/4.2 消息规范化统一在 native backend，避免两套 parser 漂移 |
| 输出契约 | feat 的 JSONL | main 的 `chat_messages.json` 不再作为第二份真相 |
| 许可证 | MIT + NOTICE | 沿用 feat；main 代码移植处保留来源声明 |
| Skill 名 | `wechat-export` | 沿用 feat；main 的 `wechat-chatlog` 废弃 |
| 单入口 | `wechat-export.mjs` 自动识别 4.1/4.2 | `wechat41-export.mjs` / `wechat42-export.mjs` 退为内部实现 |
| 4.1 回退 | 暂保留 `--legacy-weflow` | 只保留一个版本，4.1 真机验收通过后再评估移除 |

## 3. Phase 0：基线固化（已完成）

- [x] 提交 feat 工作区全部改动：`80c7573`
- [x] 打 tag `baseline-feat-2026-11-19`
- [x] 归档 main：tag `archive-main-python-4.1-2026-11-18`
- [x] 建立本计划与 TODO 跟踪表
- [x] 基线验证：`npm test` 47 项通过，`npm run test:py` 29 项通过

## 4. Phase 1：能力移植

顺序：可信度 → 兼容性 → 内容 → 体验。每项独立提交，先补测试再改行为，同步更新 STATUS/compat。

### P0：可信度与兼容性

| # | 能力 | 来源 | 落到哪 | 验收 |
| --- | --- | --- | --- | --- |
| 1 ✅ | `verify --deep` 按 `local_id + server_id` 回源库对账 | main | feat audit/verify | 2026-11-19 完成；合成库 0 missing / 0 extra |
| 2 ⏸ | 4.1 真机对齐（图片 key、媒体布局、hardlink/resource 表） | main | feat 4.1 native | 2026-11-20 用户确认先不管 4.1；代码保留，暂缓验收 |
| 3 ✅ | 引用消息 `refermsg` 还原 | main | Python 解析层 | 2026-11-19 完成；HTML/JSONL 可验证发送者/时间/原文 |
| 4 ✅ | 合并转发 `recorditem` 内层展开 | main | Python 解析层 | 2026-11-19 完成；内层文字/文件名/发送者/时间齐全 |
| 5 | 公众号正文抓取 `--fetch-articles` | main | 可选网络模块 | 默认关闭；域名白名单 + 重定向复核 |
| 6 | 表情 CDN 补下载 `--download-stickers` | main | 可选网络模块 | 默认关闭；只处理白名单域名 |

> 2026-11-19 核查：本机运行微信 4.2.0.13，唯一账号目录是 4.2 数据；`Program Files` 的 4.1.5.30 只是残留安装。第 2 项需要外部 4.1.x 账号数据/备份才能验收，暂缓。

实现约束：

- 4.1：先用 main 的 `lib/v4db.py` / `lib/wxdata.py` / `lib/keyscan.py` 做对照，决定修补还是替换 feat 的 `v41db.py`；**禁止两套并存**。
- 解析：只落在 Python native 层；Node 的 `parse.mjs` 不再重复实现引用/转发还原。
- 网络：复用 main 的 `wxexport.py` 域名白名单、重定向复核、userinfo 拒绝；不联网也能跑全量测试。
- 许可证：移植函数保留来源注释，根 NOTICE 增补。

### P1：体验与收编

| # | 能力 | 来源 | 落到哪 | 验收 |
| --- | --- | --- | --- | --- |
| 7 | `decrypt-image` 单文件调试入口 | main | Python 工具 | 单文件可解，有测试 |
| 8 ✅ | 单入口（4.2 为准） | feat | `wechat-export.mjs` | 用户入口统一为 `wechat42-export.mjs`；4.1 暂缓，不做自动分发 |
| 9 | 输出 schema 统一 | 双方 | bundle/manifest | 只保留一套 JSONL 字段，补 main 的 `missing.reason` |
| 10 ✅ | 文档/测试收编 | main | feat | 2026-11-20 完成：SECURITY/VERSION_MAINTENANCE/REFERENCES/THIRD_PARTY_NOTICES/NOTICE 已移植改写；CI 补 `sqlcipher3-wheels` |

## 5. Phase 2：分支收口（已完成 2026-11-20）

1. [x] 确认测试全绿、4.2 验收完成（4.1 暂缓，不再阻塞）
2. [x] 在 feat 上 `git merge -s ours main`：保留 main 历史，树只取 feat（`b53eeb5`）
3. [x] `git checkout main && git merge --ff-only feat`
4. [x] 删除 feat 分支（本地与远端）
5. [x] 旧 main 由 tag `archive-main-python-4.1-2026-11-18` 保底

最终状态：

- `main` = `b53eeb5`，包含 4.2 主线 + 旧 main 历史（merge commit）
- 本地和远端都只剩 `main`；`feat/wechat-export-skill` 已删除
- 归档 tag 仍在：`archive-main-python-4.1-2026-11-18`、`baseline-feat-2026-11-19`
- 收口时测试：52 项 Node + 41 项 Python 全绿

## 6. 验收标准（Definition of Done）

- [x] 只有一个活跃分支 `main`
- [x] 一个 Skill 目录、一个 `SKILL.md`、一个用户入口
- [x] 一套输出契约，没有并列的第二套
- [x] `npm test` + `npm run test:py` 全绿（CI 待推送后实际跑）
- [x] 4.2 真机：已有 2026-11-13/18 基线和 2026-11-19 引用/转发 canary
- [x] 4.1 暂缓（不在本次收口验收；代码保留，tag 保底）
- [ ] `verify --deep` 在 4.2 真实导出上跑一次 0 missing（合成测试已通过）
- [x] SKILL 触发词覆盖语音、表情、撤回、引用、合并转发、完整性校验、4.2
- [x] 文档只有一份真相，TODO/BACKLOG 不重复
- [x] main 来源代码/文档有 NOTICE 声明

## 7. 风险与止损

| 风险 | 应对 |
| --- | --- |
| 没有 4.1 真机数据 | 用户 2026-11-20 确认暂缓 4.1；代码保留，tag 保底，不阻塞收口 |
| 两套 parser 漂移 | Python 是唯一规范化层；评审卡住 Node 侧解析 |
| 输出格式不兼容 | 不做双格式；需要时写一次性转换器 |
| 移植变重写 | 只搬函数/模块，不搬整个 `wxexport.py`；每项独立提交可回退 |
| 许可证混用 | 文件头保留来源；根许可证固定 MIT + NOTICE |
| 合并期间再次分叉 | Phase 0 后所有工作按本表提交，不再在 main 上开发 |
