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
