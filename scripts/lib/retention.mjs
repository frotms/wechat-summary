import { promises as fs } from 'node:fs'
import { join, resolve } from 'node:path'

export function retentionInfo(days, now = new Date()) {
  const n = Number(days)
  if (!Number.isInteger(n) || n < 1) throw new Error('retention days 必须是正整数')
  const deleteAfter = new Date(now.getTime() + n * 86400 * 1000)
  return {
    days: n,
    deleteAfter: deleteAfter.toISOString(),
  }
}

async function readManifest(dir) {
  try {
    return JSON.parse(await fs.readFile(join(dir, 'manifest.json'), 'utf8'))
  } catch {
    return null
  }
}

/** Find immediate export dirs whose manifest.retention.deleteAfter has passed. */
export async function findExpiredExports(root, now = new Date()) {
  const base = resolve(root)
  const entries = await fs.readdir(base, { withFileTypes: true }).catch(() => [])
  const expired = []
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const dir = join(base, entry.name)
    const manifest = await readManifest(dir)
    const after = manifest?.retention?.deleteAfter
    if (!after) continue
    const time = Date.parse(after)
    if (Number.isFinite(time) && time <= now.getTime()) {
      expired.push({ dir, name: entry.name, deleteAfter: after })
    }
  }
  return expired
}

export async function removeExpiredExports(root, options = {}) {
  const expired = await findExpiredExports(root, options.now || new Date())
  if (!options.yes) return { removed: [], expired }
  const removed = []
  for (const item of expired) {
    await fs.rm(item.dir, { recursive: true, force: true })
    removed.push(item)
  }
  return { removed, expired }
}
