# Vendor notice

本目录包含从以下 MIT 项目复制/改写的核心模块：

- 项目：https://github.com/TangGod991021/wechat-exporter
- 上游 commit：`cc718ef14fd815c9d04c2035b53cee0ee0539e36`
- License：MIT（见同目录 `LICENSE`）
- 复制日期：2026-11-17
- 修改：仅把 `app.core.*` 导入改为包内相对导入，便于在本 skill 内运行。

文件：

| 文件 | 用途 |
| --- | --- |
| `keys.py` | 微信 4.2 `Config.Cipher` / 明文缓存密钥提取 |
| `decrypt.py` | SQLCipher 4 逐页解密 |
| `message_parser.py` | 4.x 消息 XML/appmsg 解析 |
| `export.py` | `ChatStore`、会话/消息/媒体读取 |
| `media.py` | 图片 V2 / WxAM、语音 SILK、视频索引等媒体处理 |
| `proc.py` | Windows 子进程无窗口调用 |

上游若更新，优先更新本目录并跑 `doctor`/金丝雀；不要把本目录当长期不动的黑盒。
