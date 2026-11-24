# 微信版本升级维护手册（4.2.x → 4.3+）

目标：微信升级后，快速判断“哪里变了”，用最小改动恢复 4.2 导出，并确认没有静默导出错数据。

> 当前维护范围：**微信 4.2.x**。4.1 代码保留在仓库，但暂不维护/不验证；旧实现归档在 tag
> `archive-main-python-4.1-2026-11-18`，需要时再回看。

## 0. 依赖边界

本 skill 的 4.2 路径默认自包含：

- Node：参数解析、目标选择、打包、HTML/Markdown、audit 编排。
- Python：`native-backend.py` 读取 `work/`，vendor 负责 key/解密/消息解析/媒体。
- `scripts/vendor/wechat42/`：来自 MIT 项目 `TangGod991021/wechat-exporter`，上游 commit
  `cc718ef14fd815c9d04c2035b53cee0ee0539e36`。
- `weflow-cli`：只在 `--legacy-weflow` 回退时使用，不是默认依赖。
- 运行时依赖：Node 22.13+、Python 3.10+、`cryptography`、`zstandard`、`sqlcipher3-wheels`（4.1/4.2 原生）、
  `silk-python`（语音）、`imageio-ffmpeg`（表情 wxgf）。

升级时优先更新 **vendor** 和 **native 层**，不要先改外部工具。

## 1. 会随版本变化的层

| 层 | 当前实现 | 版本敏感点 | 升级后可能的变化 |
| --- | --- | --- | --- |
| 4.2 密钥 | `vendor/wechat42/keys.py`：`Config.Cipher` 配置对象 + 掩码 XOR + 每库 HMAC 校验 | 配置字符串、XOR 掩码、对象/指针偏移、key 存储形态 | 字符串/掩码/偏移变化；key 改为新配置对象或更强保护 |
| SQLCipher | `vendor/wechat42/decrypt.py`、`wechat42-sandbox.py` | 页大小、KDF 算法/迭代、HMAC、保留区、WAL | 参数变化；明文头/加密头变化 |
| DB schema | `vendor/wechat42/export.py`（`ChatStore`） | `SessionTable`、`Msg_<md5(username)>`、`Name2Id`、`message_content`、`packed_info_data`、`real_sender_id` | 表名/列名/分片/压缩方式/类型编码变化 |
| 消息解析 | `vendor/wechat42/message_parser.py` + `scripts/lib/parse.mjs` | 复合 `local_type`（高 32 位子类型）、appmsg XML、`refermsg`、`recorditem` | 新增类型、XML 结构调整、引用/转发字段变化 |
| 媒体布局 | `native-backend.py`、`wechat42-media-map.py`、`lib/sticker.py`、`lib/v2_decode.py` | `msg/attach`、`msg/video`、`msg/file`、`hardlink.db`、`media_*.db`、表情缓存 | 目录/表名变化；图片 V2/WXGF、视频 V2 格式变化 |
| 表情 seed | `lib/sticker.py` + 进程内存 | seed 派生、缓存布局 | seed 来源/派生公式变化 |
| 打包/输出 | `scripts/lib/bundle.mjs`、`html.mjs`、`markdown.mjs` | `messages.jsonl` 字段、媒体引用 | 本 skill 自己的契约，升级时保持稳定 |
| 完整性校验 | `scripts/wechat-audit.py` | 表名、时间窗、`local_id/server_id` | schema 变化会影响计数/深度对账 |

## 2. 升级后第一步：诊断

```bash
node scripts/wechat42-export.mjs doctor
node scripts/wechat42-export.mjs status
node scripts/wechat42-export.mjs check-updates
```

重点看：

- `config.workDir` / `venvPython` / `toolDir` 是否正常；
- `checks` 里 `workDir`、`venvPython`、`sandboxConfig` 状态；
- `upstreamCommit` 是否还是 `cc718ef...`；
- 微信账号目录、视频目录是否还能发现。

需要重新提取 key / 重建 work 时：

```bash
node scripts/wechat42-export.mjs setup
```

> 4.2 会把逐库 raw key 缓存在 `<baseDir>/keys.json`（离线复用）。微信升级或换账号后缓存可能失效；`setup` 会检测失败并自动回退到进程提取。要强制重新提取，删除该文件或运行 `clean --purge-keys` 后再 `setup`。

导出时加 `--refresh` 可以强制重建 sandbox（legacy 路径）。

## 3. 小范围金丝雀

不要直接全量导出。先选一个小会话：

```bash
node scripts/wechat42-export.mjs \
  --target "<群ID或wxid>" --days 1 --types all \
  --output "<临时目录>"

node scripts/wechat42-export.mjs audit --output "<临时目录>" --deep
```

检查：

- `audit.json.success` 是否为 true；
- `deep.missingCount` / `extraCount` / `exportedNotInRawCount` 是否为 0；
- HTML 里文字、图片、文件、视频、语音、表情、引用、合并转发是否正常；
- `manifest.json` 的媒体状态有没有大面积 `missing`；
- 终端有没有 `NATIVE_*` 警告码。

如果只有图片/视频/表情失败，按层排查；如果文字/消息数都对不上，先查 key/解密/schema。

## 4. 升级排查顺序

1. **key 提取**：`doctor` + `setup`，看能否拿到全部库的 key；HMAC 是否通过。
2. **SQLCipher 解密**：`work/` 是否生成、`message_*.db` 是否可读；`native-backend.py check`。
3. **schema**：用 sqlite3 打开 `work/` 的 `session/session.db`、`message/message_*.db`，确认
   `SessionTable`、`Msg_*`、`Name2Id`、关键列还在。
4. **消息解析**：小范围导出，检查 `messages.jsonl` 类型分布、`localId/serverId`、引用/转发字段。
5. **媒体**：先文字，再图片/文件，最后视频/语音/表情；每一步都跑 `audit --deep`。
6. **legacy 回退**：如果 native 路径暂时不可用，可用 `--legacy-weflow` 对照；但这仍是旧路径，
   不要把它当成长期修复。

## 5. 参考项目

升级时先看参考项目的同版本 issue/release，但不要把它们的二进制/DLL 直接引入运行时。
完整清单见 [`REFERENCES.md`](REFERENCES.md) 和 `DESIGN.md` 的“后续微信版本升级参考库与排查手册”。

优先级：

1. `TangGod991021/wechat-exporter`（vendor 上游）
2. `zhuobichen/weflow-cli`（legacy 读取/导出）
3. `Tencent/wcdb`（SQLCipher/WCDB 语义）
4. 其他 4.x 工具只作为格式/思路参考

## 6. 发布前检查

- [ ] `npm test` 全绿（81 项 Node）
- [ ] `npm run test:py` 全绿（62 项 Python）
- [ ] 4.2 真机小范围金丝雀 + `audit --deep` 通过
- [ ] `compat.json` 更新微信版本、上游 commit、验证结果
- [ ] 更新 `STATUS.md` 的基线和已知限制
- [ ] 更新 `skill.json` changelog 和版本号
- [ ] 不把 `work/`、sandbox、`keys.json`、导出物提交到 Git

## 7. 回滚

- vendor 更新失败：`git checkout <previous-vendor-commit> -- scripts/vendor/wechat42/`
- 配置里的 `upstreamCommit` 只是记录，不会自动更新代码；用 `check-updates` 对比后再决定。
- 4.1 旧实现：`git show archive-main-python-4.1-2026-11-18:<path>` 取回，不直接合并分支。
