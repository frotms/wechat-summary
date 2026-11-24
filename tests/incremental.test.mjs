import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { makeWorkspace } from './helpers.mjs'
import { checkIncrementalBundle, checkIncrementalOutput } from '../scripts/lib/incremental.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = dirname(here)
const mainScript = join(repoRoot, 'scripts', 'wechat-export.mjs')
const fakeWeFlow = join(repoRoot, 'tests', 'fixtures', 'fake-weflow.mjs')

function ts(day, hour, minute) {
  return Math.floor(new Date(2026, 0, day, hour, minute, 0).getTime() / 1000)
}
const T1 = ts(5, 10, 0)
const T2 = ts(5, 10, 1)
const T3 = ts(5, 10, 2)
const T4 = ts(5, 10, 3)
const T5 = ts(6, 9, 0)
const T6 = ts(6, 9, 1)
const T7 = ts(6, 9, 2)

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

function baseArgs(ws) {
  return [
    '--weflow', fakeWeFlow,
    '--weflow-config', ws.configPath,
    '--video-root', ws.accountRoot,
    '--output', ws.outDir,
    '--json',
  ]
}

function readJsonl(path) {
  return readFileSync(path, 'utf8')
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line))
}

test('incremental export appends new messages and deduplicates media', async () => {
  const ws = makeWorkspace()
  try {
    const first = run(ws, [
      ...baseArgs(ws),
      '--target', '项目群',
      '--from', '2026-01-05', '--to', '2026-01-05',
      '--types', 'all',
      '--media-map', ws.combinedMapPath,
      '--revoke-map', ws.revokeMapPath,
    ])
    assert.equal(first.status, 0, first.stderr)
    assert.equal(first.json.totals.messages, 4)
    const firstDir = join(ws.outDir, first.json.bundles[0].dir)
    assert.equal(readJsonl(join(firstDir, 'messages.jsonl')).length, 4)
    const firstImages = readdirSync(join(firstDir, 'media', 'images')).sort()
    const firstVideos = readdirSync(join(firstDir, 'media', 'videos')).sort()
    assert.equal(firstImages.length, 2)
    assert.equal(firstVideos.length, 1)

    const second = run(ws, [
      ...baseArgs(ws),
      '--target', '项目群',
      '--incremental',
      '--types', 'all',
      '--media-map', ws.combinedMapPath,
      '--revoke-map', ws.revokeMapPath,
    ])
    assert.equal(second.status, 0, second.stderr)
    assert.equal(second.json.totals.messages, 7)
    const bundle = second.json.bundles[0]
    const bundleDir = join(ws.outDir, bundle.dir)
    const records = readJsonl(join(bundleDir, 'messages.jsonl'))
    assert.equal(records.length, 7)
    assert.deepEqual(records.map((record) => record.id), [
      `1:${T1}`, `2:${T2}`, `3:${T3}`, `4:${T4}`,
      `5:${T5}`, `6:${T6}`, `7:${T7}`,
    ])
    assert.equal(new Set(records.map((record) => record.id)).size, 7)

    const report = JSON.parse(readFileSync(join(bundleDir, 'incremental.json'), 'utf8'))
    assert.equal(report.previousMessages, 4)
    assert.equal(report.fetchedMessages, 7)
    assert.equal(report.newMessages, 3)
    assert.equal(report.duplicateMessages, 4)
    assert.equal(report.totalMessages, 7)
    assert.equal(report.check.ok, true)

    const state = JSON.parse(readFileSync(join(bundleDir, 'state.json'), 'utf8'))
    assert.equal(state.lastTimestamp, T7)
    assert.equal(state.lastMessageId, `7:${T7}`)
    assert.equal(state.messageCount, 7)

    // Media is deduplicated by sha256, not appended under _2 names.
    assert.deepEqual(readdirSync(join(bundleDir, 'media', 'images')).sort(), firstImages)
    assert.equal(readdirSync(join(bundleDir, 'media', 'files')).length, 1)
    assert.deepEqual(readdirSync(join(bundleDir, 'media', 'videos')).sort(), firstVideos)
    assert.equal(readdirSync(join(bundleDir, 'media', 'voice')).length, 1)
    assert.equal(readdirSync(join(bundleDir, 'media', 'stickers')).length, 1)
    const mediaIndex = JSON.parse(readFileSync(join(bundleDir, 'media', 'index.json'), 'utf8'))
    assert.ok(mediaIndex.items.length > 0)
    assert.ok(mediaIndex.items.every((item) => /^[0-9a-f]{64}$/.test(item.sha256)))

    const check = await checkIncrementalBundle(bundleDir)
    assert.equal(check.ok, true)
    assert.equal(check.incremental, true)

    const cli = spawnSync(process.execPath, [
      join(repoRoot, 'scripts', 'wechat-incremental-check.mjs'),
      '--output', ws.outDir,
      '--json',
    ], { encoding: 'utf8', windowsHide: true, timeout: 60_000 })
    assert.equal(cli.status, 0, cli.stderr)
    const cliReport = JSON.parse(cli.stdout)
    assert.equal(cliReport.ok, true)
    assert.equal(cliReport.incrementalBundles, 1)
  } finally {
    ws.cleanup()
  }
})

test('incremental check detects state behind messages', async () => {
  const ws = makeWorkspace()
  try {
    const first = run(ws, [
      ...baseArgs(ws),
      '--target', '项目群',
      '--from', '2026-01-05', '--to', '2026-01-05',
      '--types', 'text',
    ])
    assert.equal(first.status, 0, first.stderr)
    const bundleDir = join(ws.outDir, first.json.bundles[0].dir)

    const second = run(ws, [
      ...baseArgs(ws),
      '--target', '项目群',
      '--incremental',
      '--types', 'text',
    ])
    assert.equal(second.status, 0, second.stderr)

    const statePath = join(bundleDir, 'state.json')
    const state = JSON.parse(readFileSync(statePath, 'utf8'))
    state.lastTimestamp = 1
    writeFileSync(statePath, JSON.stringify(state, null, 2), 'utf8')

    const check = await checkIncrementalBundle(bundleDir)
    assert.equal(check.ok, false)
    assert.ok(check.problems.some((problem) => problem.includes('state')))

    const output = await checkIncrementalOutput(ws.outDir)
    assert.equal(output.ok, false)
    assert.equal(output.failed, 1)
  } finally {
    ws.cleanup()
  }
})

test('incremental requires an explicit output', () => {
  const ws = makeWorkspace()
  try {
    const res = run(ws, [
      '--weflow', fakeWeFlow,
      '--weflow-config', ws.configPath,
      '--video-root', ws.accountRoot,
      '--json',
      '--target', '项目群',
      '--incremental',
    ])
    assert.notEqual(res.status, 0)
    assert.equal(res.json?.code, 'INCREMENTAL_NEEDS_OUTPUT')
  } finally {
    ws.cleanup()
  }
})
