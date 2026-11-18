import test from 'node:test'
import assert from 'node:assert/strict'
import { hasFaces, renderFaces } from '../scripts/lib/faces.mjs'

test('known WeChat face codes become emoji', () => {
  assert.equal(renderFaces('[玫瑰]'), '🌹')
  assert.equal(renderFaces('[握手]'), '🤝')
  assert.equal(renderFaces('[月亮]'), '🌙')
  assert.equal(renderFaces('收到[玫瑰]'), '收到🌹')
})

test('unknown bracketed text is left untouched', () => {
  assert.equal(renderFaces('[图片]'), '[图片]')
  assert.equal(renderFaces('[文件]'), '[文件]')
  assert.equal(renderFaces('[abc]'), '[abc]')
  assert.equal(renderFaces('[]'), '[]')
  assert.equal(renderFaces('没有表情'), '没有表情')
})

test('the longest face name wins', () => {
  // `[左哼哼]` must not be shadowed by a shorter prefix match: the whole
  // three-character name is replaced, not a two-character prefix of it.
  const rendered = renderFaces('[左哼哼]')
  assert.notEqual(rendered, '[左哼哼]')
  assert.equal(rendered, renderFaces('[左哼哼]'))
  assert.ok(!rendered.includes('左哼哼'))
})

test('hasFaces only reports known codes', () => {
  assert.equal(hasFaces('好好[玫瑰]'), true)
  assert.equal(hasFaces('好好[图片]'), false)
  assert.equal(hasFaces(''), false)
})

test('non-string input does not throw', () => {
  assert.equal(renderFaces(undefined), '')
  assert.equal(renderFaces(null), '')
})
