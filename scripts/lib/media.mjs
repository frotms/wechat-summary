import { promises as fs } from 'node:fs'
import { createHash } from 'node:crypto'
import { basename, dirname, join } from 'node:path'

const ILLEGAL = /[\u0000-\u001f<>:"/\\|?*]/g

export function safeName(name, fallback = 'file', maxLength = 140) {
  let text = String(name ?? '')
    .replace(ILLEGAL, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\.+/, '')
    .replace(/[. ]+$/g, '')
  if (!text) text = fallback
  // Windows device names are reserved even with an extension.
  if (/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(text)) text = `_${text}`
  if (text.length > maxLength) {
    const ext = /\.[A-Za-z0-9]{1,10}$/.exec(text)?.[0] || ''
    text = text.slice(0, Math.max(1, maxLength - ext.length)) + ext
  }
  return text
}

export function humanBytes(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

export async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true })
}

export async function pathExists(path) {
  try {
    await fs.access(path)
    return true
  } catch {
    return false
  }
}

/** SHA-256 of a file, used to deduplicate media across incremental runs. */
export async function sha256File(path) {
  const hash = createHash('sha256')
  const handle = await fs.open(path, 'r')
  try {
    const buffer = Buffer.allocUnsafe(1024 * 1024)
    let position = 0
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, position)
      if (!bytesRead) break
      hash.update(buffer.subarray(0, bytesRead))
      position += bytesRead
    }
  } finally {
    await handle.close()
  }
  return hash.digest('hex')
}

export async function readJson(path) {
  const text = await fs.readFile(path, 'utf8')
  return JSON.parse(text)
}

export async function writeJson(path, value, pretty = true) {
  await ensureDir(dirname(path))
  await fs.writeFile(path, JSON.stringify(value, null, pretty ? 2 : 0) + '\n', 'utf8')
}

export async function writeText(path, text) {
  await ensureDir(dirname(path))
  await fs.writeFile(path, text, 'utf8')
}

/**
 * Hard-link when possible, copy otherwise. Hard links keep the `raw/` export
 * and `media/` bundle from doubling disk usage on the same volume.
 */
export async function linkOrCopy(source, target) {
  await ensureDir(dirname(target))
  if (await pathExists(target)) return
  try {
    await fs.link(source, target)
    return
  } catch (error) {
    if (error?.code === 'EEXIST') return
  }
  await fs.copyFile(source, target)
}

export async function moveOrCopy(source, target) {
  await ensureDir(dirname(target))
  if (await pathExists(target)) return
  try {
    await fs.rename(source, target)
  } catch {
    await fs.copyFile(source, target)
    await fs.unlink(source).catch(() => {})
  }
}

export function pathToPosix(path) {
  return String(path).replace(/\\/g, '/')
}

/** Percent-encode each path segment so `%`, `#`, `?` and CJK are safe in URLs. */
export function encodePath(path) {
  return String(path || '').split('/').map((segment) => encodeURIComponent(segment)).join('/')
}

export function baseWithoutExt(path) {
  const name = basename(path)
  const index = name.lastIndexOf('.')
  return index > 0 ? name.slice(0, index) : name
}

export function extOf(path) {
  const name = basename(path)
  const index = name.lastIndexOf('.')
  return index > 0 ? name.slice(index) : ''
}

export async function unlinkQuiet(path) {
  try {
    await fs.unlink(path)
  } catch {
    // Missing files are fine here.
  }
}

export async function rmQuiet(path) {
  try {
    await fs.rm(path, { recursive: true, force: true })
  } catch {
    // Best-effort cleanup.
  }
}

export function joinRelative(...parts) {
  return pathToPosix(join(...parts))
}
