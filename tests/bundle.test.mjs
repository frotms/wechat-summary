import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildTargetBundle } from '../scripts/lib/bundle.mjs'

test('bundle carries quote and merged-forward records into JSONL', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-bundle-'))
  try {
    const bundleDir = join(root, 'bundle')
    const rawDir = join(root, 'raw')
    await mkdir(rawDir, { recursive: true })
    const rawMessages = [
      {
        localId: 1,
        serverId: 's1',
        createTime: 1767578400,
        isSend: 0,
        senderUsername: 'wxid_lisi',
        sender: '李四',
        messageType: 'quote',
        parsedContent: '[引用] 回复 | 引用自 张三：原来的消息',
        quote: { sender: '张三', text: '原来的消息', kind: 'text' },
        content: '',
        rawContent: '',
      },
      {
        localId: 2,
        serverId: 's2',
        createTime: 1767578460,
        isSend: 0,
        senderUsername: 'wxid_lisi',
        sender: '李四',
        messageType: 'chat_history',
        parsedContent: '[聊天记录] 群聊的聊天记录 · 2 条',
        records: [
          { kind: 'text', sender: '张三', time: '10:00', text: '你好' },
          { kind: 'file', sender: '李四', time: '10:01', text: '报告.pdf',
            media: 'C:\\secret\\报告.pdf' },
        ],
        content: '',
        rawContent: '',
      },
    ]
    await buildTargetBundle({
      target: { id: '123@chatroom', name: '项目群', kind: 'group' },
      rawMessages,
      rawDir,
      mediaManifest: null,
      mediaMap: null,
      revokeMap: null,
      videoIndex: null,
      contactMap: new Map([['wxid_lisi', '李四']]),
      bundleDir,
      types: new Set(['text', 'quote', 'history']),
      mediaState: null,
      warnings: [],
      meta: {},
    })

    const lines = (await readFile(join(bundleDir, 'messages.jsonl'), 'utf8'))
      .trim().split('\n').map((line) => JSON.parse(line))
    assert.equal(lines.length, 2)
    assert.equal(lines[0].type, 'quote')
    assert.equal(lines[0].quote.sender, '张三')
    assert.equal(lines[0].quote.text, '原来的消息')
    assert.equal(lines[1].type, 'history')
    assert.equal(lines[1].records.length, 2)
    assert.equal(lines[1].records[0].text, '你好')
    assert.equal(lines[1].records[1].text, '报告.pdf')
    assert.equal(lines[1].records[1].media, undefined)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
