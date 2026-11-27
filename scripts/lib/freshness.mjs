import {
  promises as fs,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { join, relative, resolve } from 'node:path'

export const STAMP_SCHEMA = 'wechat-export-source-stamp/v1'
export const STAMP_NAME = '.source-stamp.json'

function relPosix(root, abs) {
  return relative(root, abs).split(/[\\/]/).join('/')
}

function relevantSource(rel) {
  if (rel.includes('.db.factory/')) return false
  if (rel.endsWith('.db-shm')) return false
  return /^(?:message|general|contact|session|media|hardlink|head_image|emoticon)\//.test(rel)
}

async function walkFiles(root) {
  const out = []
  async function walk(dir) {
    let entries
    try {
      entries = await fs.readdir(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const abs = join(dir, entry.name)
      if (entry.isDirectory()) await walk(abs)
      else if (entry.isFile()) out.push(abs)
    }
  }
  await walk(root)
  return out
}

function walkFilesSync(root) {
  const out = []
  function walk(dir) {
    let entries
    try {
      entries = readdirSync(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const abs = join(dir, entry.name)
      if (entry.isDirectory()) walk(abs)
      else if (entry.isFile()) out.push(abs)
    }
  }
  walk(root)
  return out
}

/**
 * Signature of the source databases. Include WAL/SHM because a message can
 * land there before SQLite checkpoints the main .db file.
 */
export async function sourceFingerprint(accountRoot, keysFile = '') {
  const dataRoot = join(resolve(accountRoot), 'db_storage')
  const rows = []
  for (const abs of await walkFiles(dataRoot)) {
    if (!/\.(?:db|db-wal|db-shm)$/i.test(abs)) continue
    if (!relevantSource(relPosix(dataRoot, abs))) continue
    let stat
    try {
      stat = await fs.stat(abs)
    } catch {
      continue
    }
    rows.push({
      path: relPosix(dataRoot, abs),
      size: stat.size,
      mtimeMs: Math.round(stat.mtimeMs),
    })
  }
  rows.sort((a, b) => a.path.localeCompare(b.path))
  let keys = { path: '', size: 0, mtimeMs: 0 }
  if (keysFile) {
    try {
      const stat = await fs.stat(keysFile)
      keys = { path: resolve(keysFile), size: stat.size, mtimeMs: Math.round(stat.mtimeMs) }
    } catch {
      // A missing cache is normal on the first run.
    }
  }
  return JSON.stringify({ schema: STAMP_SCHEMA, files: rows, keys })
}

export function sourceFingerprintSync(accountRoot, keysFile = '') {
  const dataRoot = join(resolve(accountRoot), 'db_storage')
  const rows = []
  for (const abs of walkFilesSync(dataRoot)) {
    if (!/\.(?:db|db-wal|db-shm)$/i.test(abs)) continue
    if (!relevantSource(relPosix(dataRoot, abs))) continue
    let stat
    try {
      stat = statSync(abs)
    } catch {
      continue
    }
    rows.push({
      path: relPosix(dataRoot, abs),
      size: stat.size,
      mtimeMs: Math.round(stat.mtimeMs),
    })
  }
  rows.sort((a, b) => a.path.localeCompare(b.path))
  let keys = { path: '', size: 0, mtimeMs: 0 }
  if (keysFile) {
    try {
      const stat = statSync(keysFile)
      keys = { path: resolve(keysFile), size: stat.size, mtimeMs: Math.round(stat.mtimeMs) }
    } catch {
      // A missing cache is normal on the first run.
    }
  }
  return JSON.stringify({ schema: STAMP_SCHEMA, files: rows, keys })
}

export function stampPath(baseDir) {
  return join(resolve(baseDir), STAMP_NAME)
}

export async function readStamp(baseDir) {
  try {
    const data = JSON.parse(await fs.readFile(stampPath(baseDir), 'utf8'))
    return data && data.schema === STAMP_SCHEMA ? data : null
  } catch {
    return null
  }
}

export function readStampSync(baseDir) {
  try {
    const data = JSON.parse(readFileSync(stampPath(baseDir), 'utf8'))
    return data && data.schema === STAMP_SCHEMA ? data : null
  } catch {
    return null
  }
}

function stampValue(payload) {
  return {
    schema: STAMP_SCHEMA,
    ...payload,
    updatedAt: new Date().toISOString(),
  }
}

export async function writeStamp(baseDir, payload) {
  const value = stampValue(payload)
  await fs.mkdir(resolve(baseDir), { recursive: true })
  await fs.writeFile(stampPath(baseDir), `${JSON.stringify(value, null, 2)}\n`, 'utf8')
  return value
}

export function writeStampSync(baseDir, payload) {
  const value = stampValue(payload)
  mkdirSync(resolve(baseDir), { recursive: true })
  writeFileSync(stampPath(baseDir), `${JSON.stringify(value, null, 2)}\n`, 'utf8')
  return value
}

export async function workIsFresh(config) {
  const stamp = await readStamp(config.baseDir)
  if (!stamp || resolve(stamp.accountRoot || '') !== resolve(config.accountRoot)) return false
  const current = await sourceFingerprint(config.accountRoot, join(config.baseDir, 'keys.json'))
  return stamp.fingerprint === current
}

export function workIsFreshSync(config) {
  const stamp = readStampSync(config.baseDir)
  if (!stamp || resolve(stamp.accountRoot || '') !== resolve(config.accountRoot)) return false
  const current = sourceFingerprintSync(config.accountRoot, join(config.baseDir, 'keys.json'))
  return stamp.fingerprint === current
}
