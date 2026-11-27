import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  readStamp,
  sourceFingerprint,
  sourceFingerprintSync,
  workIsFresh,
  workIsFreshSync,
  writeStamp,
} from '../scripts/lib/freshness.mjs'

test('source fingerprint tracks db, wal and keys changes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-freshness-'))
  try {
    const account = join(root, 'account')
    const base = join(root, 'base')
    const work = join(base, 'work')
    await mkdir(join(account, 'db_storage', 'message'), { recursive: true })
    await mkdir(work, { recursive: true })
    await writeFile(join(account, 'db_storage', 'message', 'message_0.db'), 'db')
    await writeFile(join(account, 'db_storage', 'message', 'message_0.db-wal'), 'wal')
    await writeFile(join(base, 'keys.json'), '{}')
    const config = { baseDir: base, workDir: work, accountRoot: account }

    const asyncFingerprint = await sourceFingerprint(account, join(base, 'keys.json'))
    assert.equal(asyncFingerprint, sourceFingerprintSync(account, join(base, 'keys.json')))

    await writeStamp(base, {
      accountRoot: account,
      workDir: work,
      fingerprint: asyncFingerprint,
    })
    assert.equal(await workIsFresh(config), true)
    assert.equal(workIsFreshSync(config), true)

    await writeFile(join(account, 'db_storage', 'message', 'message_0.db-wal'), 'wal2')
    assert.equal(await workIsFresh(config), false)
    assert.equal(workIsFreshSync(config), false)
    assert.ok(await readStamp(base))
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
