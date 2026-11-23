import test from 'node:test'
import assert from 'node:assert/strict'
import { parseArgv } from '../scripts/lib/args.mjs'
import { UsageError } from '../scripts/lib/errors.mjs'

test('parseArgv keeps repeatable target flags', () => {
  const { opts } = parseArgv(['--group', 'A', '--group', 'B', '--target=x', '--days', '7'])
  assert.deepEqual(opts.group, ['A', 'B'])
  assert.deepEqual(opts.target, ['x'])
  assert.equal(opts.days, '7')
})

test('parseArgv accepts opt-in article/sticker flags', () => {
  const { opts } = parseArgv(['--fetch-articles', '--download-stickers'])
  assert.equal(opts.fetchArticles, true)
  assert.equal(opts.downloadStickers, true)
})

test('parseArgv supports --no-* booleans', () => {
  const { opts } = parseArgv(['--no-html', '--no-video-scan', '--no-media'])
  assert.equal(opts.html, false)
  assert.equal(opts.videoScan, false)
  assert.equal(opts.media, false)
})

test('parseArgv rejects unknown flags', () => {
  assert.throws(() => parseArgv(['--frm', '2026-01-01']), UsageError)
})

test('parseArgv rejects a missing value', () => {
  assert.throws(() => parseArgv(['--from']), UsageError)
})

test('parseArgv supports --flag=false', () => {
  const { opts } = parseArgv(['--html=false', '--json=true'])
  assert.equal(opts.html, false)
  assert.equal(opts.json, true)
})
