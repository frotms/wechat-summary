import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildText,
  classifyMessage,
  linkFromMessage,
  parseFileAppmsg,
  parseGroupSenderPrefix,
  parseVideoMeta,
  resolveSender,
} from '../scripts/lib/parse.mjs'
import { parseVideoMd5Candidates } from '../scripts/lib/video.mjs'

const VIDEO_MD5 = 'abcdef0123456789abcdef0123456789'

test('classifyMessage recognises text, image, file, video', () => {
  assert.equal(classifyMessage({ localType: 1, messageType: 'text' }).type, 'text')
  assert.equal(classifyMessage({ localType: 3, messageType: 'image' }).type, 'image')
  assert.equal(classifyMessage({ localType: 43, messageType: 'video' }).type, 'video')
  const file = classifyMessage({
    localType: 49,
    messageType: 'link',
    rawContent: '<msg><appmsg><title>a.pdf</title><type>6</type><md5>x</md5></appmsg></msg>',
  })
  assert.equal(file.type, 'file')
  assert.equal(file.file.title, 'a.pdf')
})

test('quote and chat-history appmsgs are classified before link', () => {
  const quote = classifyMessage({
    localType: 57 * 0x1_0000_0000 + 49,
    messageType: 'quote',
    parsedContent: '[引用] 回复 | 引用自 张三：原来的消息',
    quote: { sender: '张三', text: '原来的消息', kind: 'text' },
  })
  assert.equal(quote.type, 'quote')
  assert.equal(
    buildText({ reply: '回复：你好', parsedContent: '[引用] 回复 | 引用自 张三：原来的消息' },
      { type: 'quote' }),
    '回复：你好',
  )

  const history = classifyMessage({
    localType: 19 * 0x1_0000_0000 + 49,
    messageType: 'chat_history',
    parsedContent: '[聊天记录] 群聊的聊天记录 · 2 条',
    records: [{ kind: 'text', sender: '张三', text: '你好' }],
  })
  assert.equal(history.type, 'history')

  // Legacy/native payloads without messageType still classify by records.
  const byRecords = classifyMessage({
    localType: 49,
    messageType: 'other',
    records: [{ kind: 'text', text: '你好' }],
  })
  assert.equal(byRecords.type, 'history')
})

test('encoded media types are classified from the XML shape', () => {
  const encoded = 21474836529 // 0x500000031 -> masks to 49
  assert.equal(classifyMessage({
    localType: encoded,
    messageType: 'other',
    content: '<msg><appmsg><img md5="11111111111111111111111111111111"/></appmsg></msg>',
  }).type, 'image')
  assert.equal(classifyMessage({
    localType: encoded,
    messageType: 'link',
    content: '<msg><appmsg><title>文章</title><url>https://example.com/a</url></appmsg></msg>',
  }).type, 'link')
  assert.equal(classifyMessage({
    localType: encoded,
    messageType: 'other',
    content: `<msg><videomsg md5="${VIDEO_MD5}"/></msg>`,
  }).type, 'video')
})

test('linkFromMessage falls back to appUrl/appTitle when rawContent is empty', () => {
  const link = linkFromMessage({
    rawContent: '',
    content: '',
    appUrl: 'https://mp.weixin.qq.com/s/example',
    appTitle: '一篇文章',
    appDescription: '摘要',
  })
  assert.deepEqual(link, {
    url: 'https://mp.weixin.qq.com/s/example',
    title: '一篇文章',
    description: '摘要',
  })
  assert.equal(linkFromMessage({ rawContent: '', appUrl: 'javascript:alert(1)', appTitle: '' }), null)
})

test('voice messages classify as voice', () => {
  assert.equal(classifyMessage({
    localType: 34,
    messageType: 'voice',
    parsedContent: '[语音 3s]',
  }).type, 'voice')
})

test('encoded file messages (high 32 bits = 6) are classified from parsedContent', () => {
  const file = classifyMessage({
    localType: 25769803825, // 6 << 32 | 49
    messageType: 'other',
    rawContent: '',
    parsedContent: '[文件] MYS-2847-SE7-Pro返工与销售审批报告-v7.html',
  })
  assert.equal(file.type, 'file')
  assert.equal(file.file.title, 'MYS-2847-SE7-Pro返工与销售审批报告-v7.html')
})

test('parseFileAppmsg reads title and size, and ignores quoted refermsg type', () => {
  const parsed = parseFileAppmsg('<msg><appmsg><title>报价单.xlsx</title><type>6</type><appattach><totallen>2048</totallen></appattach></appmsg></msg>')
  assert.equal(parsed.isFile, true)
  assert.equal(parsed.title, '报价单.xlsx')
  assert.equal(parsed.bytes, 2048)

  const quoteOnly = parseFileAppmsg('<msg><appmsg><title>回复</title><type>57</type><refermsg><content>&lt;type&gt;6&lt;/type&gt;</content></refermsg></appmsg></msg>')
  assert.equal(quoteOnly, null)
})

test('parseVideoMeta and md5 candidates handle self-sent rawmd5', () => {
  const raw = `<msg><videomsg rawmd5="${VIDEO_MD5}" playlength="12"/></msg>`
  assert.equal(parseVideoMeta(raw).playLength, '12')
  assert.deepEqual(parseVideoMd5Candidates({ rawContent: raw }), [VIDEO_MD5])
})

test('parseVideoMd5Candidates prefers videomsg md5 over a generic md5', () => {
  const raw = `<msg><videomsg md5="${VIDEO_MD5}" cdnthumbmd5="ffffffffffffffffffffffffffffffff"/></msg>`
  const candidates = parseVideoMd5Candidates({ rawContent: raw })
  assert.equal(candidates[0], VIDEO_MD5)
})

test('group sender prefix', () => {
  assert.equal(parseGroupSenderPrefix('wxid_abc123:\n你好'), 'wxid_abc123')
  assert.equal(parseGroupSenderPrefix('wxid_abc123: 你好'), 'wxid_abc123')
  assert.equal(parseGroupSenderPrefix('note: 这不是发送者'), '')
  assert.equal(parseGroupSenderPrefix('普通文本'), '')
})

test('resolveSender maps group members through contacts', () => {
  const contactMap = new Map([['wxid_lisi', '李四']])
  const group = resolveSender(
    { isSend: 0, senderUsername: 'wxid_lisi', content: '你好' },
    { target: { id: '1@chatroom', name: '项目群', kind: 'group' }, contactMap, kind: 'group' },
  )
  assert.equal(group.sender, '李四')
  assert.equal(group.isMe, false)

  const self = resolveSender(
    { isSend: 1, senderUsername: 'wxid_me' },
    { target: { id: '1@chatroom', name: '项目群', kind: 'group' }, contactMap, kind: 'group', meLabel: '我' },
  )
  assert.equal(self.sender, '我')
  assert.equal(self.isMe, true)
})

test('buildText produces readable labels', () => {
  assert.equal(buildText({ parsedContent: '你好' }, { type: 'text' }), '你好')
  assert.equal(buildText({ rawContent: `<msg><videomsg md5="${VIDEO_MD5}" playlength="12"/></msg>` }, { type: 'video' }), '[视频 12″]')
  assert.equal(buildText({}, { type: 'file', file: { title: 'a.pdf' } }), '[文件] a.pdf')
})
