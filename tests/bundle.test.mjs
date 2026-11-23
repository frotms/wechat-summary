import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildTargetBundle } from '../scripts/lib/bundle.mjs'

test('bundle carries structured transfer, red-packet, location and card fields', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-bundle-special-'))
  try {
    const bundleDir = join(root, 'bundle')
    const rawDir = join(root, 'raw')
    await mkdir(rawDir, { recursive: true })
    const base = {
      isSend: 0,
      senderUsername: 'wxid_lisi',
      sender: '李四',
      content: '',
      rawContent: '',
    }
    const rawMessages = [
      { ...base, localId: 1, serverId: 's1', createTime: 1767578400,
        messageType: 'transfer', amount: '¥88.00', appTitle: '转账',
        appDescription: '说明', parsedContent: '[转账] ¥88.00' },
      { ...base, localId: 2, serverId: 's2', createTime: 1767578460,
        messageType: 'redpacket', appTitle: '恭喜发财', appDescription: '大吉大利',
        parsedContent: '[红包] 大吉大利' },
      { ...base, localId: 3, serverId: 's3', createTime: 1767578520,
        messageType: 'location', lat: '31.2304', lng: '121.4737',
        appTitle: '人民广场', appDescription: '上海',
        appUrl: 'https://uri.amap.com/marker?position=121.4737,31.2304',
        parsedContent: '[位置] 人民广场（上海）' },
      { ...base, localId: 4, serverId: 's4', createTime: 1767578580,
        messageType: 'card', cardUsername: 'wxid_card', appTitle: '张三',
        parsedContent: '[名片] 张三' },
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
      types: new Set(['transfer', 'redpacket', 'location', 'card']),
      mediaState: null,
      warnings: [],
      meta: {},
    })

    const lines = (await readFile(join(bundleDir, 'messages.jsonl'), 'utf8'))
      .trim().split('\n').map((line) => JSON.parse(line))
    const byId = new Map(lines.map((line) => [line.localId, line]))
    assert.equal(byId.get(1).type, 'transfer')
    assert.equal(byId.get(1).amount, '¥88.00')
    assert.equal(byId.get(2).type, 'redpacket')
    assert.equal(byId.get(2).redpacket.title, '恭喜发财')
    assert.equal(byId.get(3).type, 'location')
    assert.equal(byId.get(3).location.lat, '31.2304')
    assert.match(byId.get(3).location.url, /uri\.amap\.com/)
    assert.equal(byId.get(4).type, 'card')
    assert.equal(byId.get(4).contact.username, 'wxid_card')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('bundle exposes mediaStatus and missingReason for missing media', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-bundle-missing-'))
  try {
    const bundleDir = join(root, 'bundle')
    const rawDir = join(root, 'raw')
    await mkdir(rawDir, { recursive: true })
    const rawMessages = [{
      localId: 1, serverId: 's1', createTime: 1767578400, isSend: 0,
      senderUsername: 'wxid_lisi', sender: '李四',
      messageType: 'image', parsedContent: '[图片]',
      content: '<msg><img md5="11111111111111111111111111111111"/></msg>',
      rawContent: '<msg><img md5="11111111111111111111111111111111"/></msg>',
    }]
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
      types: new Set(['image']),
      mediaState: null,
      warnings: [],
      meta: {},
    })
    const lines = (await readFile(join(bundleDir, 'messages.jsonl'), 'utf8'))
      .trim().split('\n').map((line) => JSON.parse(line))
    assert.equal(lines[0].mediaStatus, 'missing')
    assert.equal(lines[0].missingReason, 'image-not-local')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('bundle links fetched article bodies', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-bundle-article-'))
  try {
    const bundleDir = join(root, 'bundle')
    const rawDir = join(root, 'bundle', 'raw')
    await mkdir(rawDir, { recursive: true })
    const rawMessages = [{
      localId: 1, serverId: 's1', createTime: 1767578400, isSend: 0,
      senderUsername: 'wxid_lisi', sender: '李四',
      messageType: 'link', parsedContent: '文章标题',
      appUrl: 'https://mp.weixin.qq.com/s/x', appTitle: '文章标题',
      content: '', rawContent: '',
    }]
    const mediaManifest = {
      records: {
        '1:1767578400': {
          kind: 'article',
          path: 'assets/articles/a.txt',
          html: 'assets/articles/a.html',
          title: '文章标题',
          url: 'https://mp.weixin.qq.com/s/x',
        },
      },
    }
    await buildTargetBundle({
      target: { id: '123@chatroom', name: '项目群', kind: 'group' },
      rawMessages,
      rawDir,
      mediaManifest,
      mediaMap: null,
      revokeMap: null,
      videoIndex: null,
      contactMap: new Map([['wxid_lisi', '李四']]),
      bundleDir,
      types: new Set(['link']),
      mediaState: null,
      warnings: [],
      meta: {},
    })
    const lines = (await readFile(join(bundleDir, 'messages.jsonl'), 'utf8'))
      .trim().split('\n').map((line) => JSON.parse(line))
    assert.equal(lines[0].article.title, '文章标题')
    assert.equal(lines[0].article.path, 'raw/assets/articles/a.txt')
    assert.equal(lines[0].article.html, 'raw/assets/articles/a.html')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

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
    assert.equal(lines[0].mediaStatus, 'none')
    assert.equal(lines[1].type, 'history')
    assert.equal(lines[1].records.length, 2)
    assert.equal(lines[1].records[0].text, '你好')
    assert.equal(lines[1].records[1].text, '报告.pdf')
    assert.equal(lines[1].records[1].media, undefined)
    assert.equal(lines[1].mediaStatus, 'none')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
