import { closeSync, openSync, readdirSync, readSync, statSync } from 'node:fs'
import { dirname, extname, join } from 'node:path'
import { baseWithoutExt, extOf } from './media.mjs'

const VIDEO_EXTS = new Set(['.mp4', '.mov', '.m4v', '.avi', '.mkv', '.flv', '.webm'])
const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png'])
const MAX_FILES = 250_000
const MAX_DEPTH = 5
const V2_MAGIC = Buffer.from([0x07, 0x08, 0x56, 0x32, 0x08, 0x07])

/**
 * WeChat stores videos as `<md5>.mp4` (or `<md5>_raw.mp4`) under
 * `msg/video/YYYY-MM/`. Self-sent videos may only carry `rawmd5`; both are
 * collected and tried.
 */
export function parseVideoMd5Candidates(message) {
  const text = [message?.rawContent, message?.content, message?.parsedContent]
    .filter(Boolean)
    .join('\n')
  if (!text) return []

  const patterns = [
    /<videomsg[^>]*\smd5\s*=\s*["']([a-fA-F0-9]{16,64})["']/i,
    /<videomsg[^>]*\srawmd5\s*=\s*["']([a-fA-F0-9]{16,64})["']/i,
    /<videomsg[^>]*\snewmd5\s*=\s*["']([a-fA-F0-9]{16,64})["']/i,
    /<md5>([a-fA-F0-9]{16,64})<\/md5>/i,
    /\brawmd5\s*=\s*["']([a-fA-F0-9]{16,64})["']/i,
    /\bnewmd5\s*=\s*["']([a-fA-F0-9]{16,64})["']/i,
    /\bmd5\s*=\s*["']([a-fA-F0-9]{16,64})["']/i,
    /\/([a-fA-F0-9]{32})(?:[\/?"']|$)/,
  ]

  const out = []
  const seen = new Set()
  for (const pattern of patterns) {
    const match = pattern.exec(text)
    if (!match) continue
    const value = match[1].toLowerCase()
    if (seen.has(value)) continue
    seen.add(value)
    out.push(value)
  }
  return out
}

/**
 * Keep the best candidate for a key: lower rank wins (0 = normal file,
 * 1 = fallback like `_raw`/`_thumb`, 2 = hex substring match), then larger file.
 */
function addToMap(map, key, payload) {
  const normalized = String(key || '').toLowerCase()
  if (!normalized) return
  const existing = map.get(normalized)
  if (!existing) {
    map.set(normalized, payload)
    return
  }
  const rank = payload.rank ?? 0
  const existingRank = existing.rank ?? 0
  if (rank < existingRank || (rank === existingRank && payload.size > existing.size)) {
    map.set(normalized, payload)
  }
}

function walk(dir, state, depth = 0) {
  if (state.truncated || state.count >= MAX_FILES || depth > MAX_DEPTH) return
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    if (state.truncated) return
    if (entry.isSymbolicLink()) continue
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      walk(full, state, depth + 1)
      continue
    }
    if (!entry.isFile()) continue
    state.count += 1
    if (state.count >= MAX_FILES) {
      state.truncated = true
      return
    }
    const ext = extname(entry.name).toLowerCase()
    if (VIDEO_EXTS.has(ext)) {
      let size = 0
      try {
        size = statSync(full).size
      } catch {
        continue
      }
      const key = baseWithoutExt(full).toLowerCase()
      const isRaw = key.endsWith('_raw')
      addToMap(state.videos, key, { file: full, size, rank: isRaw ? 1 : 0 })
      if (isRaw) addToMap(state.videos, key.slice(0, -4), { file: full, size, rank: 1 })
      for (const hex of key.match(/[a-f0-9]{16,64}/g) || []) {
        if (hex !== key) addToMap(state.videos, hex, { file: full, size, rank: 2 })
      }
      state.files.push(full)
    } else if (IMAGE_EXTS.has(ext)) {
      let size = 0
      try {
        size = statSync(full).size
      } catch {
        continue
      }
      const key = baseWithoutExt(full).toLowerCase()
      const isThumb = key.endsWith('_thumb')
      addToMap(state.posters, key, { file: full, size, rank: isThumb ? 1 : 0 })
      if (isThumb) addToMap(state.posters, key.slice(0, -6), { file: full, size, rank: 1 })
      for (const hex of key.match(/[a-f0-9]{16,64}/g) || []) {
        if (hex !== key) addToMap(state.posters, hex, { file: full, size, rank: 2 })
      }
    }
  }
}

function buildPrefixIndex(map, prefixLength = 16) {
  const index = new Map()
  for (const key of map.keys()) {
    if (key.length < prefixLength) continue
    const prefix = key.slice(0, prefixLength)
    const list = index.get(prefix)
    if (list) list.push(key)
    else index.set(prefix, [key])
  }
  return index
}

export function buildVideoIndex(roots) {
  const state = {
    videos: new Map(),
    posters: new Map(),
    files: [],
    count: 0,
    truncated: false,
  }
  for (const root of roots || []) walk(root, state)
  return {
    videos: state.videos,
    posters: state.posters,
    files: state.files,
    scanned: state.count,
    truncated: state.truncated,
    roots: [...(roots || [])],
    videoPrefix: buildPrefixIndex(state.videos),
    posterPrefix: buildPrefixIndex(state.posters),
  }
}

/**
 * Build an index from `weflow-cli media --type video --json` rows. Used only as
 * a fallback when the account directory cannot be derived.
 */
export function buildVideoIndexFromRows(rows) {
  const videos = new Map()
  for (const row of rows || []) {
    if (!row || row.exists === false || !row.path) continue
    const key = String(row.md5 || '').toLowerCase()
    if (!key) continue
    let size = Number(row.size) || 0
    if (!size) {
      try {
        size = statSync(row.path).size
      } catch {
        size = 0
      }
    }
    videos.set(key, { file: row.path, size, rank: key.endsWith('_raw') ? 1 : 0 })
  }
  return {
    videos,
    posters: new Map(),
    files: [...videos.values()].map((entry) => entry.file),
    scanned: videos.size,
    truncated: false,
    roots: [],
    videoPrefix: buildPrefixIndex(videos),
    posterPrefix: new Map(),
  }
}

function resolveFromMap(map, prefixIndex, candidates) {
  for (const raw of candidates || []) {
    const candidate = String(raw || '').toLowerCase()
    if (!candidate) continue
    for (const key of [candidate, `${candidate}_raw`, candidate.replace(/_raw$/, '')]) {
      const hit = map.get(key)
      if (hit) return { ...hit, key }
    }
    if (candidate.length >= 16) {
      const list = prefixIndex.get(candidate.slice(0, 16)) || []
      for (const key of list) {
        if (key.startsWith(candidate) || candidate.startsWith(key.replace(/_raw$/, ''))) {
          return { ...map.get(key), key }
        }
      }
    }
  }
  return null
}

export function resolveVideo(md5Candidates, index) {
  if (!index?.videos?.size) return null
  return resolveFromMap(index.videos, index.videoPrefix, md5Candidates)
}

export function findVideoPoster(md5Candidates, videoFile, index) {
  for (const raw of md5Candidates || []) {
    const candidate = String(raw || '').toLowerCase()
    if (!candidate) continue
    for (const key of [candidate, `${candidate}_thumb`, candidate.replace(/_raw$/, '')]) {
      const hit = index?.posters?.get(key)
      if (hit) return hit.file
    }
  }
  if (videoFile) {
    const dir = dirname(videoFile)
    const base = baseWithoutExt(videoFile)
    const bases = base.endsWith('_raw') ? [base, base.slice(0, -4)] : [base, `${base}_raw`]
    for (const item of bases) {
      for (const name of [`${item}.jpg`, `${item}_thumb.jpg`, `${item}.png`]) {
        const candidate = join(dir, name)
        try {
          if (statSync(candidate).isFile()) return candidate
        } catch {
          // Try the next form.
        }
      }
    }
  }
  return null
}

export function verifyVideoFile(path) {
  let fd
  try {
    fd = openSync(path, 'r')
    const buffer = Buffer.alloc(16)
    const read = readSync(fd, buffer, 0, 16, 0)
    if (read >= 12 && buffer.subarray(4, 8).toString('latin1') === 'ftyp') {
      return { ok: true, format: 'mp4' }
    }
    if (read >= 4 && buffer.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3]))) {
      return { ok: true, format: 'webm/mkv' }
    }
    if (read >= 12 && buffer.subarray(0, 4).toString('latin1') === 'RIFF'
      && buffer.subarray(8, 12).toString('latin1') === 'AVI ') {
      return { ok: true, format: 'avi' }
    }
    if (read >= 6 && buffer.subarray(0, 6).equals(V2_MAGIC)) {
      return { ok: false, format: 'wechat-v2-encrypted', reason: 'video-encrypted-v2' }
    }
    return { ok: false, format: 'unknown', reason: 'unrecognized-video-magic' }
  } catch (error) {
    return { ok: false, format: 'unknown', reason: `read-failed:${error?.code || 'unknown'}` }
  } finally {
    if (fd !== undefined) {
      try {
        closeSync(fd)
      } catch {
        // Ignore close errors.
      }
    }
  }
}

export function videoExtension(path) {
  const ext = extOf(path).toLowerCase()
  return VIDEO_EXTS.has(ext) ? ext : '.mp4'
}

export { VIDEO_EXTS, IMAGE_EXTS, baseWithoutExt }
