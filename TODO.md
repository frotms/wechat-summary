# TODO（当前待办）

> 更新：2026-11-23 · 版本 1.11.0  
> 只列**尚未完成/尚未验证**的 skill 工作；`BACKLOG.md` 里的收藏、朋友圈、公众号正文、语音转写等已记录项不重复。  
> 定位：Claude Code Skill，**不做 GUI / 管理后台 / 工程化界面**；导出 HTML 只是浏览产物。  
> **2026-11-20**：用户确认先不管微信 4.1；4.1 条目统一标为「暂缓」，不阻塞当前收口。

## 分支收敛（P0，进行中）

> 目标：以 feat 为唯一主干吸收 main 能力，最终只保留一个 `main` 分支、一个入口、一套产物。完整计划与验收见 `docs/MERGE_PLAN.md`。

- [x] 固化 feat 基线（`80c7573`，tag `baseline-feat-2026-11-19`）
- [x] 归档 main（tag `archive-main-python-4.1-2026-11-18`）
- [x] `verify --deep` ID 对账（main → feat audit；2026-11-19 完成，含 coverage.boundary、类型/跳过校验）
- [ ]（暂缓）4.1 真机对齐（main → feat 4.1 native；与下方「4.1 真实兼容性验证」合并执行，只留一套引擎）
- [x] 引用 `refermsg` 还原（main → Python 解析层；2026-11-19 完成）
- [x] 合并转发 `recorditem` 展开（main → Python 解析层；2026-11-19 完成）
- [x] 公众号正文 / 表情 CDN 补下载（opt-in，白名单；2026-11-23 完成）
- [x] `decrypt-image` 单文件调试入口（2026-11-23 完成）
- [ ]（暂缓）单入口自动识别 4.1/4.2（4.1 暂缓；4.2 已用 `wechat42-export.mjs`）
- [x] 输出 schema 统一（JSONL `mediaStatus`/`missingReason`；`docs/OUTPUT_SCHEMA.md`）
- [x] 文档/测试收编（SECURITY/VERSION_MAINTENANCE/REFERENCES 已移植改写；selftest 场景由 feat 测试覆盖）
- [x] Phase 2 分支收口（2026-11-20，`b53eeb5`；本地/远端只剩 `main`，feat 已删除）

## P0：影响 skill 正确使用

- [x] **更新 `SKILL.md` frontmatter description**：已加入语音、表情、撤回、引用、合并转发、完整性校验、4.1/4.2 原生。
- [x] **修正 SKILL 命令示例**：已改为 4.2 `wechat42-export.mjs` / 4.1 `wechat41-export.mjs`，并注明 `wechat-export.mjs` 是内部主命令。
- [ ]（暂缓）**补 4.1 `doctor`**：检查 config、`decryptKey`/`ntKey`、`sqlcipher3`、work、sessions；给出明确下一步。
- [x] **修 native work 重建条件**：`needWork` 已包含 `nativeDefault`。
- [x] **补清理链**：4.1 `clean` 删除 `wechat41/work`/`_stickers`/映射临时文件；4.2 `clean` 删除 `baseDir/_stickers`。
- [ ]（暂缓）**4.1 work 隔离/原子化**：按账号分目录；解密写临时文件后原子改名；加 `--force`；避免半成品/mtime 跳过坏文件。
- [ ]（暂缓）**4.1 真实兼容性验证**：真实 4.1 数据跑 sessions/contacts/export/voice/sticker/revoke/audit；确认 `server_id` 列、`decryptKey` 语义、`ntKey+ntSalt` 变体。
- [ ]（暂缓）**4.1 图片链路**：验证 4.1 图片 key/缓存布局；当前 `prepare_target_images` 使用 4.2 的 `find_codes()`，真机可能全缺失。
- [x] **native 下尊重 `--no-voice-map` / `--no-sticker-map`**：4.1/4.2 native `--media` 已按环境变量跳过语音/表情。

## P1：可靠性/性能/测试

- [x] **native 缓存失效**：`_native_file_index.json` 增加 TTL（默认 600s）和强制刷新；`--refresh` 同时刷新 file/video 索引；视频索引可显式 refresh；`_native_sticker_seed` 用样本校验，失效自动重探。
- [x] **文本型导出跳过媒体/表情映射**：`--types` 不含 image/file/video/voice/emoji 时，不生成 media-map/sticker-map，也不扫 sticker seed。
- [x] **减少全媒体导出的重复映射**：types 含 image/file 时 native `--media` 已处理图片/文件/视频/封面/语音/表情，跳过 media-map；`--with-media-map` 可强制。
- [x] **4.2 离线 key 缓存**：`setup` 保存 `<baseDir>/keys.json`；后续优先缓存，缓存失效自动回退；`clean` 默认保留，`--purge-keys` 删除。
- [ ] **4.2 native 缺口**：V2 视频在无 image/file 类型且禁用 media-map 时仍可能只复制原始字节；普通 WXGF HEVC、合并转发内层媒体未覆盖。公众号正文已由 `--fetch-articles` 覆盖。
- [x] **CI 补 `sqlcipher3-wheels`**：已加入 Python CI 依赖，4.1 native 测试不再 skip。
- [ ] **补端到端测试**：4.1 wrapper、native-only setup、`--legacy-weflow` 回退、4.1 audit；当前多为手工验证。
- [x] **Skill 安装/触发验证**：已 junction 到 `~/.claude/skills/wechat-export`；当前会话已识别 `wechat-export`，安装路径下 `--check` 通过（key 缓存）。
- [x] **错误提示结构化**：4.1 解密已输出 `SQLCIPHER_MISSING`/`CONFIG_INVALID`/`CONFIG_KEY_MISSING`/`WORK_DIR_INSIDE_ACCOUNT` + hint；native 图片/语音/seed/file-index 警告带 code。

## P2：能力补全（非 UI）

- [ ]（暂缓）4.1 真机 canary（图片/表情/语音/撤回/audit）
- [ ] 真实 V2 视频、普通 WXGF HEVC 解码
- [x] 公众号正文（`--fetch-articles`）
- [ ] 合并转发内层媒体（CDN）
- [x] 红包/转账/位置/名片结构化（金额/标题/经纬度/wxid）
- [ ] 小程序等特殊消息更深字段
- [x] 联系人/群成员/头像导出（`contacts`）
- [x] 跨会话按人导出、全局时间线（`person`/`timeline`）
- [x] 全局搜索（已有 `search` 跨会话扫描）
- [ ] 按会话导出的完整增量/媒体去重（当前 `--incremental` 仅 person/timeline）
- [ ] 导出加密/脱敏/保留期
- [x] `requirements.txt`（4.2 原生/语音/表情；4.1 的 sqlcipher3 以注释给出）
- [ ] 跨平台验证（如未来需要；当前 Windows-only 是明确边界）

## 明确不做

- GUI / Web 管理后台 / 可视化控制台 / 安装向导等工程化界面
- 写入、转发、新增、删除、编辑、撤回聊天内容
- 手机端、他人设备、远程静默运行
