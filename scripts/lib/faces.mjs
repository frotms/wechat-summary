import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * WeChat's built-in faces appear in message text as `[微笑]`, `[玫瑰]`, ...
 * The client renders them with its own artwork (private-use code points), so
 * the closest standard Unicode emoji is used here instead.
 *
 * The map is derived from weflow-cli's `wechat_emoji.json` (MIT, same
 * upstream as this skill's read layer).
 */
const DATA_FILE = join(dirname(fileURLToPath(import.meta.url)), 'wechat_faces.json')

let FACES = {}
let PATTERN = null

function loadFaces() {
  if (PATTERN) return
  try {
    FACES = JSON.parse(readFileSync(DATA_FILE, 'utf8'))
  } catch {
    FACES = {}
  }
  const names = Object.keys(FACES)
  if (!names.length) {
    PATTERN = null
    return
  }
  // Longest name first, so `[左哼哼]` is never shadowed by a shorter prefix.
  const alternation = names
    .sort((a, b) => b.length - a.length)
    .map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|')
  PATTERN = new RegExp(`\\[(${alternation})\\]`, 'g')
}

/**
 * Replace known `[face]` codes with their emoji.
 *
 * Unknown codes are left untouched - `[图片]` and `[文件]` are labels this
 * skill generates, not faces.
 */
export function renderFaces(text) {
  loadFaces()
  if (!PATTERN) return String(text ?? '')
  return String(text ?? '').replace(PATTERN, (match, name) => FACES[name] || match)
}

/** True when the text contains at least one known face code. */
export function hasFaces(text) {
  loadFaces()
  if (!PATTERN) return false
  PATTERN.lastIndex = 0
  return PATTERN.test(String(text ?? ''))
}
