# 安全与隐私说明

本 skill 对微信数据**只读**：不发送、不转发、不撤回、不删除、不编辑、不写入聊天内容。导出物和临时工作目录都在微信账号目录之外。

## 1. 读取范围

- 微信账号目录下 `db_storage/` 的**副本**（4.2 解密到 `work/`；4.1 解密到 `wechat41/work/`）。
- 本地媒体目录：`msg/`、`cache/`、`emoticon/`、`business/`。
- 只读进程内存（仅当前登录账号）：
  - 4.2：从 `Weixin.exe` 的 `Config.Cipher` 提取逐库 raw key；
  - 表情包：从进程内存提取 `emoticonSeed`。
  - 只使用 `OpenProcess` / `ReadProcessMemory` / `VirtualQueryEx`；**不注入、不 hook、不 patch、不修改** `Weixin.exe`。
- 不使用截图、OCR、UI 自动化。

## 2. 密钥处理

- 4.2 的逐库 raw key 只在内存中使用；不打印到 stdout/stderr，不写入导出目录。
- sandbox 模式下，`<baseDir>/home/.weflow-cli/config.json` 含 **sandbox 密钥**（不是原微信 key），由 `clean` 删除。
- 4.1 的 `decryptKey` / `ntKey+ntSalt` 只读使用；不打印。
- `doctor` / `status` 只报告配置、work、venv 状态和密钥数量，不输出密钥内容。
- 不要把 `~/.wechat-export/` 下的配置、工作目录、导出物提交到 Git 或上传。

## 3. 网络

默认**不联网**。当前版本：

- 导出、audit、媒体解码、转发/引用解析全部本地完成。
- `check-updates` 只执行 `git ls-remote`，不下载、不执行上游代码。
- 尚未启用公众号正文抓取、表情 CDN 下载。未来若启用，必须满足：
  - HTTPS/HTTP GET，不发送聊天内容、密钥或账号凭据；
  - 请求前和重定向后都检查域名白名单；
  - 拒绝带 userinfo（`user@host`）的 URL；
  - 响应大小上限；
  - 默认关闭，显式参数开启。
- 不上传聊天记录、媒体、`work/`、`sandbox/` 或导出目录。

## 4. 本地产物

| 路径 | 内容 | 清理 |
| --- | --- | --- |
| `~/.wechat-export/<时间戳>-wechat42/` | 导出结果（明文） | 手动删除 |
| `<baseDir>/work/` | 4.2 解密库（明文） | `wechat42-export.mjs clean` |
| `<baseDir>/sandbox/` | legacy 重加密 sandbox | `wechat42-export.mjs clean` |
| `<baseDir>/home/` | sandbox 配置（含 sandbox 密钥） | `wechat42-export.mjs clean` |
| `<baseDir>/_stickers/` | 表情包解码缓存 | `wechat42-export.mjs clean` |
| `~/.wechat-export/wechat41/{work,_stickers}` | 4.1 临时数据（暂不维护） | `wechat41-export.mjs clean` |

- `<baseDir>` 默认 `~/.wechat-export/wechat42`，可由 `setup --base-dir` 指定。
- `clean` 使用普通文件删除，不是安全擦除；高敏感数据建议放在启用磁盘加密的卷上。
- `audit.json`、`manifest.json`、`messages.jsonl`、`revokes.json` 都含真实消息/路径，按敏感数据处理。

## 5. 日志与报告

- 进度和错误只包含计数、标题、媒体状态；正文不写入 stderr。
- 结构化错误会带 `code` / `hint`（如 `SQLCIPHER_MISSING`、`CONFIG_KEY_MISSING`），不包含密钥。
- `audit --deep` 报告包含消息 `local_id` / `server_id` 和路径，不包含完整正文；导出文件本身含正文。
- 分享日志/报告前先脱敏账号目录、群名、文件路径。

## 6. 完整性校验的边界

- `audit` / `audit --deep` 只证明**本机源库与导出**的一致性。
- 不能证明从未同步到这台电脑的消息存在。
- 不能证明解析器对所有消息类型都完全正确；两侧使用同一解析层时，解析层自身的遗漏不会被 deep 检出。
- 媒体缺失只表示本机没有缓存，不能凭空恢复；不会为此联网下载。

## 7. 只读强制

- legacy `weflow-cli` 路径有命令白名单：只允许 `check` / `sessions` / `contacts` / `media` / `export json|md`；`send` / `forward` / `delete` / `edit` / `revoke` / `config` / `fav` / `assistant` 在启动前拒绝。
- 导出目录、`work` / `sandbox` / `home`、表情/语音输出都检查不能位于微信账号目录内；Node 与 Python 双侧校验。
- 图片/文件硬链接指向 `raw/` 导出副本；视频、表情包复制，不硬链接微信源文件。

## 8. 报告问题

如果发现写入微信数据、上传内容、密钥落盘或路径越界，请立即停止使用，保留日志并删除 `~/.wechat-export/` 下的工作目录。
