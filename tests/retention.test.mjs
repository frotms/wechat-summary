import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { findExpiredExports, removeExpiredExports, retentionInfo } from '../scripts/lib/retention.mjs'

async function makeExport(root, name, deleteAfter) {
  const dir = join(root, name)
  await mkdir(dir, { recursive: true })
  await writeFile(join(dir, 'manifest.json'), JSON.stringify({
    schema: 'wechat-export-batch/v1',
    retention: { days: 7, deleteAfter },
  }), 'utf8')
  return dir
}

test('retention metadata and expired export discovery', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-retention-'))
  try {
    const info = retentionInfo(7, new Date('2026-01-01T00:00:00Z'))
    assert.equal(info.days, 7)
    assert.equal(info.deleteAfter, '2026-01-08T00:00:00.000Z')

    const expiredDir = await makeExport(root, 'old', '2026-01-01T00:00:00.000Z')
    const futureDir = await makeExport(root, 'new', '2099-01-01T00:00:00.000Z')
    const now = new Date('2026-06-01T00:00:00Z')
    const expired = await findExpiredExports(root, now)
    assert.deepEqual(expired.map((item) => item.name), ['old'])

    const dry = await removeExpiredExports(root, { now, yes: false })
    assert.equal(dry.removed.length, 0)
    assert.ok(existsSync(expiredDir))

    const done = await removeExpiredExports(root, { now, yes: true })
    assert.equal(done.removed.length, 1)
    assert.equal(existsSync(expiredDir), false)
    assert.ok(existsSync(futureDir))
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
