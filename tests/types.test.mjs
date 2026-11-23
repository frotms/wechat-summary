import test from 'node:test'
import assert from 'node:assert/strict'
import { typesNeedMedia, typesNeedNativeAssets } from '../scripts/lib/types.mjs'

test('text-only type sets do not need media mapping', () => {
  assert.equal(typesNeedMedia('text,quote,history'), false)
  assert.equal(typesNeedMedia('text, link, location, card, call'), false)
  assert.equal(typesNeedMedia('文字,引用,聊天记录'), false)
})

test('media-bearing type sets need media mapping', () => {
  assert.equal(typesNeedMedia(undefined), true)
  assert.equal(typesNeedMedia(''), true)
  assert.equal(typesNeedMedia('all'), true)
  assert.equal(typesNeedMedia('text,voice'), true)
  assert.equal(typesNeedMedia('图片,文件'), true)
  assert.equal(typesNeedMedia('video'), true)
  assert.equal(typesNeedMedia('emoji'), true)
})

test('native asset types are the ones that trigger export --media', () => {
  assert.equal(typesNeedNativeAssets(undefined), true)
  assert.equal(typesNeedNativeAssets('all'), true)
  assert.equal(typesNeedNativeAssets('image'), true)
  assert.equal(typesNeedNativeAssets('text,文件'), true)
  assert.equal(typesNeedNativeAssets('video'), false)
  assert.equal(typesNeedNativeAssets('voice'), false)
  assert.equal(typesNeedNativeAssets('emoji'), false)
  assert.equal(typesNeedNativeAssets('text,quote,history'), false)
})
