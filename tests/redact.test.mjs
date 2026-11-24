import test from 'node:test'
import assert from 'node:assert/strict'
import { redactRecords, redactText } from '../scripts/lib/redact.mjs'

test('basic redaction masks phone and email but keeps ID', () => {
  const text = '电话 13800138000，邮箱 a.b@example.com，身份证 11010519491231002X'
  const out = redactText(text, 'basic')
  assert.match(out, /\[手机号\]/)
  assert.match(out, /\[邮箱\]/)
  assert.match(out, /11010519491231002X/)
})

test('strict redaction masks ID and bank card too', () => {
  const text = '身份证 11010519491231002X，卡号 6222021234567890123'
  const out = redactText(text, 'strict')
  assert.match(out, /\[身份证\]/)
  assert.match(out, /\[银行卡\]/)
})

test('redactRecords clones and masks nested fields', () => {
  const records = [{
    id: '1:1',
    text: '联系 13800138000',
    quote: { text: '邮箱 a@b.com' },
    miniprogram: { title: '电话 13900139000', description: '' },
    records: [{ text: '身份证 11010519491231002X' }, { title: '报告 13800138000' }],
    rawContent: '<msg>13800138000</msg>',
    media: {},
  }]
  const out = redactRecords(records, 'strict')
  assert.equal(records[0].text, '联系 13800138000')
  assert.match(out[0].text, /\[手机号\]/)
  assert.match(out[0].quote.text, /\[邮箱\]/)
  assert.match(out[0].miniprogram.title, /\[手机号\]/)
  assert.match(out[0].records[0].text, /\[身份证\]/)
  assert.match(out[0].records[1].title, /\[手机号\]/)
  assert.match(out[0].rawContent, /\[手机号\]/)
})
