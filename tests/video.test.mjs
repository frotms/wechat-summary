import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  buildVideoIndex,
  findVideoPoster,
  resolveVideo,
  verifyVideoFile,
} from '../scripts/lib/video.mjs'
import { VIDEO_MD5, jpegBytes, makeWorkspace, mp4Bytes } from './helpers.mjs'

test('buildVideoIndex resolves md5 to mp4 and poster', () => {
  const ws = makeWorkspace()
  try {
    const index = buildVideoIndex([ws.videoRoot])
    const hit = resolveVideo([VIDEO_MD5], index)
    assert.ok(hit)
    assert.ok(hit.file.endsWith(`${VIDEO_MD5}.mp4`))
    const poster = findVideoPoster([VIDEO_MD5], hit.file, index)
    assert.ok(poster)
    assert.ok(poster.endsWith(`${VIDEO_MD5}.jpg`))
    assert.equal(verifyVideoFile(hit.file).ok, true)
  } finally {
    ws.cleanup()
  }
})

test('_raw videos are found and normal mp4 is preferred', () => {
  const ws = makeWorkspace()
  try {
    const dir = join(ws.videoRoot, '2026-02')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, `${VIDEO_MD5}_raw.mp4`), mp4Bytes())
    const index = buildVideoIndex([ws.videoRoot])
    const hit = resolveVideo([VIDEO_MD5], index)
    assert.ok(hit.file.endsWith(`${VIDEO_MD5}.mp4`))
    const rawOnly = 'deadbeefdeadbeefdeadbeefdeadbeef'
    writeFileSync(join(dir, `${rawOnly}_raw.mp4`), mp4Bytes())
    const rawIndex = buildVideoIndex([ws.videoRoot])
    const rawHit = resolveVideo([rawOnly], rawIndex)
    assert.ok(rawHit)
    assert.ok(rawHit.file.endsWith(`${rawOnly}_raw.mp4`))
  } finally {
    ws.cleanup()
  }
})

test('verifyVideoFile flags invalid bytes', () => {
  const ws = makeWorkspace()
  try {
    const bad = join(ws.root, 'bad.bin')
    writeFileSync(bad, Buffer.from('not a video'))
    const result = verifyVideoFile(bad)
    assert.equal(result.ok, false)
  } finally {
    ws.cleanup()
  }
})

test('poster lookup does not leak outside the video directory', () => {
  const ws = makeWorkspace()
  try {
    const index = buildVideoIndex([ws.videoRoot])
    const noPoster = findVideoPoster(['ffffffffffffffffffffffffffffffff'], null, index)
    assert.equal(noPoster, null)
  } finally {
    ws.cleanup()
  }
})

test('jpeg fixture is non-empty', () => {
  assert.ok(jpegBytes().length > 4)
})
