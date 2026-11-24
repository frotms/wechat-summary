import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { decryptBundle, encryptDirectory } from '../scripts/lib/crypto-bundle.mjs'

test('encrypts a directory and decrypts it with the same password', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-crypto-'))
  try {
    const input = join(root, 'bundle')
    await mkdir(join(input, 'media', 'images'), { recursive: true })
    await writeFile(join(input, 'messages.jsonl'), '{"id":"1"}\n', 'utf8')
    await writeFile(join(input, 'media', 'images', 'a.jpg'), Buffer.from([0xff, 0xd8, 0xff, 0xd9]))
    const encrypted = join(root, 'bundle.wxenc')
    const encryptedResult = await encryptDirectory(input, encrypted, 'pass-123')
    assert.equal(encryptedResult.files, 2)

    const output = join(root, 'restored')
    const decrypted = await decryptBundle(encrypted, output, 'pass-123')
    assert.equal(decrypted.files, 2)
    assert.equal(await readFile(join(output, 'messages.jsonl'), 'utf8'), '{"id":"1"}\n')
    assert.deepEqual(
      [...(await readFile(join(output, 'media', 'images', 'a.jpg')))],
      [0xff, 0xd8, 0xff, 0xd9],
    )
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('wrong password and tampering are rejected', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wechat-crypto-bad-'))
  try {
    const input = join(root, 'bundle')
    await mkdir(input, { recursive: true })
    await writeFile(join(input, 'a.txt'), 'secret', 'utf8')
    const encrypted = join(root, 'bundle.wxenc')
    await encryptDirectory(input, encrypted, 'right')
    await assert.rejects(() => decryptBundle(encrypted, join(root, 'wrong'), 'wrong'))

    const bytes = await readFile(encrypted)
    bytes[bytes.length - 1] ^= 0xff
    await writeFile(encrypted, bytes)
    await assert.rejects(() => decryptBundle(encrypted, join(root, 'tampered'), 'right'))
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
