import test from 'node:test'
import assert from 'node:assert/strict'
import { renderHtml } from '../scripts/lib/html.mjs'
import { renderMarkdown } from '../scripts/lib/markdown.mjs'

function fixtureRecord(overrides = {}) {
  return {
    id: '1:1767578400',
    seq: 1,
    localId: 1,
    serverId: 'srv-1',
    timestamp: 1767578400,
    localTime: '2026-01-05 10:00:00',
    day: '2026-01-05',
    senderId: '',
    sender: '李四',
    isMe: false,
    type: 'quote',
    typeLabel: '引用',
    text: '[引用] 回复 | 引用自 张三：原来的消息',
    media: {},
    missing: [],
    quote: { sender: '张三', text: '原来的消息', kind: 'text' },
    records: [
      { kind: 'text', sender: '张三', time: '10:00', text: '你好' },
      { kind: 'file', sender: '李四', time: '10:01', text: '报告.pdf' },
    ],
    isNote: false,
    revoked: false,
    ...overrides,
  }
}

const target = { id: '123@chatroom', name: '项目群', kind: 'group' }
const manifest = { query: { label: '2026-01-05' }, stats: {} }

test('HTML renders quote and merged-forward records', () => {
  const html = renderHtml({ target, records: [fixtureRecord()], manifest })
  assert.match(html, /class="quote"/)
  assert.match(html, /引用 张三/)
  assert.match(html, /原来的消息/)
  assert.match(html, /聊天记录 · 2 条/)
  assert.match(html, /你好/)
  assert.match(html, /报告\.pdf/)
})

test('Markdown renders quote and merged-forward records', () => {
  const md = renderMarkdown({ target, records: [fixtureRecord()], manifest })
  assert.match(md, /> 引用 张三：原来的消息/)
  assert.match(md, /> 张三 10:00 你好/)
  assert.match(md, /> 李四 10:01 报告\.pdf/)
})

test('history type uses the chat-history label', () => {
  const html = renderHtml({
    target,
    records: [fixtureRecord({ type: 'history', typeLabel: '聊天记录' })],
    manifest,
  })
  assert.match(html, /type-history/)
  assert.match(html, /聊天记录 · 2 条/)
})

test('article bodies render as local links', () => {
  const record = fixtureRecord({
    type: 'link',
    text: '文章标题',
    quote: null,
    records: [],
    link: { title: '文章标题', url: 'https://mp.weixin.qq.com/s/x' },
    article: {
      title: '文章标题',
      url: 'https://mp.weixin.qq.com/s/x',
      path: 'raw/assets/articles/a.txt',
    },
  })
  const html = renderHtml({ target, records: [record], manifest })
  assert.match(html, /class="article-card"/)
  assert.match(html, /raw\/assets\/articles\/a\.txt/)
  const md = renderMarkdown({ target, records: [record], manifest })
  assert.match(md, /\[📄 文章标题\]\(raw\/assets\/articles\/a\.txt\)/)
})

test('special messages render structured cards and links', () => {
  const records = [
    fixtureRecord({
      type: 'transfer', text: '[转账] ¥88.00', amount: '¥88.00',
      quote: null, records: [],
    }),
    fixtureRecord({
      type: 'redpacket', text: '[红包] 大吉大利',
      redpacket: { title: '恭喜发财', description: '大吉大利' },
      quote: null, records: [],
    }),
    fixtureRecord({
      type: 'location', text: '[位置] 人民广场（上海）',
      location: {
        lat: '31.2304', lng: '121.4737', title: '人民广场', city: '上海',
        url: 'https://uri.amap.com/marker?position=121.4737,31.2304',
      },
      quote: null, records: [],
    }),
    fixtureRecord({
      type: 'card', text: '[名片] 张三',
      contact: { username: 'wxid_card', title: '张三' },
      quote: null, records: [],
    }),
  ]
  const html = renderHtml({ target, records, manifest })
  assert.match(html, /class="special transfer"/)
  assert.match(html, /¥88\.00/)
  assert.match(html, /class="special redpacket"/)
  assert.match(html, /恭喜发财/)
  assert.match(html, /class="special location"/)
  assert.match(html, /uri\.amap\.com/)
  assert.match(html, /class="special card"/)
  assert.match(html, /wxid_card/)

  const md = renderMarkdown({ target, records, manifest })
  assert.match(md, /\[📍 人民广场\]\(https:\/\/uri\.amap\.com/)
  assert.match(md, /名片账号：`wxid_card`/)
})

test('HTML and Markdown render mini-program cards', () => {
  const record = fixtureRecord({
    type: 'miniprogram',
    typeLabel: '小程序',
    text: '[小程序] 示例小程序',
    quote: undefined,
    records: [],
    miniprogram: {
      title: '示例小程序',
      description: '小程序描述',
      appName: '示例应用',
      appId: 'wx123456',
      pagePath: 'pages/index?id=1',
      username: 'gh_example',
      iconUrl: 'https://example.com/icon.png',
      url: 'https://mp.weixin.qq.com/s/example',
      appType: '33',
    },
  })
  const html = renderHtml({ target, records: [record], manifest })
  assert.match(html, /🧩 示例小程序/)
  assert.match(html, /示例应用 · wx123456 · pages\/index\?id=1/)
  assert.match(html, /https:\/\/mp\.weixin\.qq\.com\/s\/example/)
  const md = renderMarkdown({ target, records: [record], manifest })
  assert.match(md, /🧩 \*\*示例小程序\*\*/)
  assert.match(md, /示例应用 · wx123456/)
})

test('HTML and Markdown render forwarded inner media', () => {
  const record = fixtureRecord({
    type: 'history',
    typeLabel: '聊天记录',
    quote: undefined,
    records: [
      { kind: 'image', sender: '张三', time: '10:00', text: '[图片]', media: 'media/images/fwd.jpg', mediaStatus: 'exported' },
      { kind: 'file', sender: '张三', time: '10:01', text: '[文件] 报告.pdf', title: '报告.pdf', media: 'media/files/报告.pdf', mediaStatus: 'exported' },
      { kind: 'video', sender: '张三', time: '10:02', text: '[视频]', mediaStatus: 'missing', missingReason: 'forward-media-not-local' },
    ],
  })
  const html = renderHtml({ target, records: [record], manifest })
  assert.match(html, /class="forward-image"/)
  assert.match(html, /media\/images\/fwd\.jpg/)
  assert.match(html, /报告\.pdf/)
  assert.match(html, /\[视频未导出\]/)
  const md = renderMarkdown({ target, records: [record], manifest })
  assert.match(md, /!\[转发图片\]/)
  assert.match(md, /📄 报告\.pdf/)
  assert.match(md, /\[视频未导出\]/)
})
