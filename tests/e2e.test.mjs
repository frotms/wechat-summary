import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { VIDEO_MD5, makeWorkspace } from './helpers.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = dirname(here)
const mainScript = join(repoRoot, 'scripts', 'wechat-export.mjs')
const fakeWeFlow = join(repoRoot, 'tests', 'fixtures', 'fake-weflow.mjs')

function run(ws, args) {
  const res = spawnSync(process.execPath, [mainScript, ...args], {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 120_000,
    env: { ...process.env, FAKE_WEFLOW_FIXTURE: ws.fixturePath },
  })
  let json = null
  try {
    json = JSON.parse(res.stdout)
  } catch {
    json = null
  }
  return { status: res.status, stdout: res.stdout, stderr: res.stderr, json }
}

function baseArgs(ws, output = ws.outDir) {
  return [
    '--weflow', fakeWeFlow,
    '--weflow-config', ws.configPath,
    '--video-root', ws.accountRoot,
    '--output', output,
    '--json',
  ]
}

function readJsonl(path) {
  return readFileSync(path, 'utf8')
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line))
}

test('exports a group with text, image, file and video', () => {
  const ws = makeWorkspace()
  try {
    const res = run(ws, [
      ...baseArgs(ws),
      '--target', '项目群',
      '--from', '2026-01-05', '--to', '2026-01-06',
      '--types', 'all',
      '--media-map', ws.combinedMapPath,
      '--revoke-map', ws.revokeMapPath,
    ])
    assert.equal(res.status, 0, res.stderr)
    assert.ok(res.json, res.stdout)
    assert.equal(res.json.success, true)
    assert.equal(res.json.totals.targets, 1)
    assert.equal(res.json.totals.messages, 7)

    const bundle = res.json.bundles[0]
    const bundleDir = join(ws.outDir, bundle.dir)
    const records = readJsonl(join(bundleDir, 'messages.jsonl'))
    assert.equal(records.length, 7)

    const text = records.find((record) => record.type === 'text')
    assert.equal(text.sender, '李四')
    assert.equal(text.text, '大家好')
    assert.equal(text.revoked, true)
    assert.equal(text.revoke.kind, 'batch')
    assert.equal(text.revoke.batchId, 123456)
    assert.equal(text.revoke.msgUniqueId, `123@chatroom_${text.timestamp}_1`)

    const image = records.find((record) => record.type === 'image')
    assert.ok(image.media.image)
    assert.ok(image.media.image.startsWith('media/images/'))

    const file = records.find((record) => record.type === 'file')
    assert.equal(file.fileName, '报告.pdf')
    assert.ok(file.media.file.startsWith('media/files/'))

    const video = records.find((record) => record.type === 'video')
    assert.equal(video.videoMd5, VIDEO_MD5)
    assert.ok(video.media.video)
    assert.ok(video.media.video.startsWith('media/videos/'))
    assert.ok(video.media.poster)
    assert.ok(video.media.poster.startsWith('media/images/'))

    const voice = records.find((record) => record.type === 'voice')
    assert.ok(voice.media.voice)
    assert.ok(voice.media.voice.startsWith('media/voice/'))

    const sticker = records.find((record) => record.type === 'emoji')
    assert.ok(sticker, 'emoji 记录应存在')
    assert.ok(sticker.media.sticker)
    assert.ok(sticker.media.sticker.startsWith('media/stickers/'))
    assert.equal(sticker.stickerMd5, 'abcdef0123456789abcdef0123456789')

    const html = readFileSync(join(bundleDir, 'index.html'), 'utf8')
    assert.match(html, /<video/)
    assert.match(html, /<audio/)
    assert.match(html, /chat-sticker/)
    assert.match(html, /revoked-badge/)
    assert.match(html, /media\/videos\//)
    assert.match(html, /media\/voice\//)
    assert.match(html, /media\/stickers\//)
    const markdown = readFileSync(join(bundleDir, 'transcript.md'), 'utf8')
    assert.match(markdown, /报告\.pdf/)
    assert.match(markdown, /🎤 语音/)
    assert.match(markdown, /!\[表情\]/)
    assert.match(markdown, /已撤回/)
    const revokes = JSON.parse(readFileSync(join(bundleDir, 'revokes.json'), 'utf8'))
    assert.equal(revokes.items.length, 1)
    assert.equal(revokes.items[0].messageId, `1:${text.timestamp}`)
    assert.equal(revokes.orphans.length, 1)
    assert.equal(revokes.orphans[0].content, '孤立撤回样本')

    const videoFiles = readdirSync(join(bundleDir, 'media', 'videos'))
    assert.equal(videoFiles.length, 1)
    const videoBytes = readFileSync(join(bundleDir, 'media', 'videos', videoFiles[0]))
    assert.equal(videoBytes.subarray(4, 8).toString('latin1'), 'ftyp')

    const fileFiles = readdirSync(join(bundleDir, 'media', 'files'))
    assert.equal(fileFiles.length, 1)
    assert.match(fileFiles[0], /报告\.pdf$/)

    const voiceFiles = readdirSync(join(bundleDir, 'media', 'voice'))
    assert.equal(voiceFiles.length, 1)

    const stickerFiles = readdirSync(join(bundleDir, 'media', 'stickers'))
    assert.equal(stickerFiles.length, 1)

    const manifest = JSON.parse(readFileSync(join(bundleDir, 'manifest.json'), 'utf8'))
    assert.equal(manifest.stats.media.images.found, 1)
    assert.equal(manifest.stats.media.files.found, 1)
    assert.equal(manifest.stats.media.videos.found, 1)
    assert.equal(manifest.stats.media.voices.found, 1)
    assert.equal(manifest.stats.media.posters.found, 1)
    assert.equal(manifest.stats.media.emojis.found, 1)
    assert.equal(manifest.stats.revoked, 1)
  } finally {
    ws.cleanup()
  }
})

test('exports a person and resolves sender name', () => {
  const ws = makeWorkspace()
  try {
    const res = run(ws, [
      ...baseArgs(ws),
      '--person', '张三',
      '--from', '2026-01-05', '--to', '2026-01-05',
      '--types', 'all',
    ])
    assert.equal(res.status, 0, res.stderr)
    assert.equal(res.json.totals.messages, 2)
    const bundleDir = join(ws.outDir, res.json.bundles[0].dir)
    const records = readJsonl(join(bundleDir, 'messages.jsonl'))
    assert.ok(records.every((record) => record.sender === '张三'))
    assert.equal(records.filter((record) => record.type === 'image').length, 1)
  } finally {
    ws.cleanup()
  }
})

test('--list hides official accounts by default', () => {
  const ws = makeWorkspace()
  try {
    const res = run(ws, [...baseArgs(ws), '--list'])
    assert.equal(res.status, 0, res.stderr)
    assert.equal(res.json.success, true)
    assert.equal(res.json.count, 3)
    const names = res.json.sessions.map((session) => session.name)
    assert.deepEqual(names.sort(), ['张三', '项目群', '特殊群'].sort())
    assert.ok(res.json.sessions.every((session) => session.summary === undefined))
  } finally {
    ws.cleanup()
  }
})

test('exports quotes, forwards and special messages', () => {
  const ws = makeWorkspace()
  try {
    const res = run(ws, [
      ...baseArgs(ws),
      '--target', '特殊群',
      '--from', '2026-01-05', '--to', '2026-01-05',
      '--types', 'quote,history,transfer,redpacket,location,card',
    ])
    assert.equal(res.status, 0, res.stderr)
    assert.ok(res.json, res.stdout)
    const bundle = res.json.bundles[0]
    const bundleDir = join(ws.outDir, bundle.dir)
    const records = readJsonl(join(bundleDir, 'messages.jsonl'))
    assert.equal(records.length, 6)
    const byType = Object.fromEntries(records.map((record) => [record.type, record]))

    assert.equal(byType.quote.quote.sender, '张三')
    assert.equal(byType.quote.quote.text, '原来的消息')
    assert.equal(byType.history.records.length, 2)
    assert.equal(byType.history.records[1].text, '报告.pdf')
    assert.equal(byType.transfer.amount, '¥88.00')
    assert.equal(byType.redpacket.redpacket.title, '恭喜发财')
    assert.equal(byType.location.location.lat, '31.2304')
    assert.match(byType.location.location.url, /uri\.amap\.com/)
    assert.equal(byType.card.contact.username, 'wxid_card')

    const html = readFileSync(join(bundleDir, 'index.html'), 'utf8')
    assert.match(html, /class="quote"/)
    assert.match(html, /<details class="record"/)
    assert.match(html, /class="special transfer"/)
    assert.match(html, /class="special redpacket"/)
    assert.match(html, /class="special location"/)
    assert.match(html, /class="special card"/)
  } finally {
    ws.cleanup()
  }
})

test('--types text skips media extraction', () => {
  const ws = makeWorkspace()
  const output = join(ws.root, 'out-text')
  try {
    const res = run(ws, [
      ...baseArgs(ws, output),
      '--target', '项目群',
      '--from', '2026-01-05', '--to', '2026-01-06',
      '--types', 'text',
    ])
    assert.equal(res.status, 0, res.stderr)
    assert.equal(res.json.totals.messages, 2)
    const bundleDir = join(output, res.json.bundles[0].dir)
    const records = readJsonl(join(bundleDir, 'messages.jsonl'))
    assert.ok(records.every((record) => record.type === 'text'))
    const html = readFileSync(join(bundleDir, 'index.html'), 'utf8')
    assert.doesNotMatch(html, /<video/)
  } finally {
    ws.cleanup()
  }
})

test('--dry-run does not create the output directory', () => {
  const ws = makeWorkspace()
  const output = join(ws.root, 'out-dry')
  try {
    const res = run(ws, [
      ...baseArgs(ws, output),
      '--target', '项目群',
      '--days', '3',
      '--dry-run',
    ])
    assert.equal(res.status, 0, res.stderr)
    assert.equal(res.json.dryRun, true)
    assert.equal(res.json.targets.length, 1)
    assert.equal(existsSync(output), false)
  } finally {
    ws.cleanup()
  }
})

test('missing video directory is reported, not guessed', () => {
  const ws = makeWorkspace()
  const output = join(ws.root, 'out-novideo')
  try {
    const res = run(ws, [
      ...baseArgs(ws, output),
      '--target', '项目群',
      '--from', '2026-01-05', '--to', '2026-01-05',
      '--types', 'all',
      '--no-video-scan',
    ])
    assert.equal(res.status, 0, res.stderr)
    const bundleDir = join(output, res.json.bundles[0].dir)
    const records = readJsonl(join(bundleDir, 'messages.jsonl'))
    const video = records.find((record) => record.type === 'video')
    assert.equal(video.media.video, undefined)
    assert.ok(video.missing.some((item) => item.reason === 'video-root-not-found'))
    assert.equal(res.json.totals.media.videos.missing, 1)
  } finally {
    ws.cleanup()
  }
})

test('--all-official exports official account sessions', () => {
  const ws = makeWorkspace()
  const output = join(ws.root, 'out-official')
  try {
    const res = run(ws, [
      ...baseArgs(ws, output),
      '--all-official',
      '--from', '2026-01-05', '--to', '2026-01-05',
      '--types', 'text',
    ])
    assert.equal(res.status, 0, res.stderr)
    assert.equal(res.json.totals.targets, 1)
    assert.equal(res.json.totals.messages, 1)
    assert.equal(res.json.bundles[0].target.id, 'gh_news')
  } finally {
    ws.cleanup()
  }
})

test('--check reports weflow, python and video readiness', () => {
  const ws = makeWorkspace()
  try {
    const res = run(ws, [
      '--weflow', fakeWeFlow,
      '--weflow-config', ws.configPath,
      '--video-root', ws.accountRoot,
      '--check',
      '--json',
    ])
    assert.equal(res.status, 0, res.stderr)
    assert.equal(res.json.ready, true)
    assert.equal(res.json.mediaReady, true)
    assert.equal(res.json.wechat.videoFiles, 1)
    assert.equal(res.json.weflow.version, '9.9.9')
  } finally {
    ws.cleanup()
  }
})

test('refuses an output directory inside the WeChat account', () => {
  const ws = makeWorkspace()
  const output = join(ws.accountRoot, 'exported')
  try {
    const res = run(ws, [
      ...baseArgs(ws, output),
      '--target', '项目群',
      '--from', '2026-01-05', '--to', '2026-01-06',
      '--types', 'text',
    ])
    assert.equal(res.status, 2, res.stderr)
    assert.equal(res.json.success, false)
    assert.equal(res.json.code, 'OUTPUT_INSIDE_WECHAT')
    assert.equal(existsSync(output), false)
  } finally {
    ws.cleanup()
  }
})
