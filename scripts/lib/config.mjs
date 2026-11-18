import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

export function weflowConfigPath() {
  return join(homedir(), '.weflow-cli', 'config.json')
}

/**
 * Read only the non-secret path fields. `config.json` also contains key
 * material; this function deliberately never returns or logs those fields.
 */
export function readWeFlowConfig(pathOverride) {
  const path = pathOverride ? resolve(expandHome(pathOverride)) : weflowConfigPath()
  try {
    const data = JSON.parse(readFileSync(path, 'utf8'))
    return {
      path,
      exists: true,
      ntDbPath: typeof data.ntDbPath === 'string' ? expandHome(data.ntDbPath) : '',
      dbPath: typeof data.dbPath === 'string' ? expandHome(data.dbPath) : '',
      dbPath3x: typeof data.dbPath3x === 'string' ? expandHome(data.dbPath3x) : '',
      wxid: typeof data.wxid === 'string' ? data.wxid : '',
    }
  } catch {
    return { path, exists: false, ntDbPath: '', dbPath: '', dbPath3x: '', wxid: '' }
  }
}

export function expandHome(path) {
  const text = String(path || '')
  if (text === '~') return homedir()
  if (text.startsWith('~/') || text.startsWith('~\\')) return join(homedir(), text.slice(2))
  return text
}

function isDir(path) {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

function safeReaddir(path) {
  try {
    return readdirSync(path, { withFileTypes: true })
  } catch {
    return []
  }
}

function pushUnique(list, seen, path) {
  if (!path) return
  const absolute = resolve(path)
  const key = process.platform === 'win32' ? absolute.toLowerCase() : absolute
  if (seen.has(key)) return
  seen.add(key)
  list.push(absolute)
}

/**
 * Account roots are the directory that contains `db_storage/` and `msg/`
 * (WeChat 4.x) or `Msg3.0.db` / `FileStorage/` (WeChat 3.x).
 */
export function deriveWeChatRoots(config, options = {}) {
  const roots = []
  const seen = new Set()

  for (const explicit of options.explicitRoots || []) {
    if (explicit) pushUnique(roots, seen, expandHome(explicit))
  }

  if (config?.ntDbPath) {
    // <account>/db_storage/message/message_0.db -> <account>
    pushUnique(roots, seen, dirname(dirname(dirname(config.ntDbPath))))
    pushUnique(roots, seen, dirname(dirname(config.ntDbPath)))
  }
  if (config?.dbPath) {
    pushUnique(roots, seen, dirname(config.dbPath))
  }
  if (config?.dbPath3x) {
    pushUnique(roots, seen, dirname(config.dbPath3x))
  }

  for (const dir of options.checkDataDirs || []) {
    if (dir) pushUnique(roots, seen, dir)
  }

  return roots.filter((root) => existsSync(root))
}

function addVideoCandidates(list, seen, base) {
  if (!base || !existsSync(base)) return
  const name = basename(base).toLowerCase()
  if (name === 'video') {
    pushUnique(list, seen, base)
    return
  }
  for (const candidate of [
    join(base, 'msg', 'video'),
    join(base, 'FileStorage', 'Video'),
    join(base, 'Msg', 'Video'),
    join(base, 'msg', 'video'),
  ]) {
    if (isDir(candidate)) pushUnique(list, seen, candidate)
  }
}

/**
 * Find directories that actually hold `msg/video` or `FileStorage/Video`.
 * The roots passed in can be account dirs, `xwechat_files` roots or an
 * explicit user-provided path.
 */
export function findVideoRoots(roots) {
  const found = []
  const seen = new Set()

  for (const root of roots || []) {
    addVideoCandidates(found, seen, root)

    // <root>/xwechat_files/<account>/msg/video
    const xwechat = join(root, 'xwechat_files')
    if (isDir(xwechat)) {
      for (const entry of safeReaddir(xwechat)) {
        if (entry.isDirectory()) addVideoCandidates(found, seen, join(xwechat, entry.name))
      }
    }

    // <root>/<account>/msg/video
    for (const entry of safeReaddir(root)) {
      if (!entry.isDirectory()) continue
      addVideoCandidates(found, seen, join(root, entry.name))
    }
  }

  return found
}

export function isPathInside(parent, child) {
  const p = resolve(parent)
  const c = resolve(child)
  const rel = relative(p, c)
  if (!rel) return true
  if (isAbsolute(rel)) return false
  return rel !== '..' && !rel.startsWith(`..${sep}`)
}

export function isSystemAccount(username) {
  const id = String(username || '').toLowerCase()
  if (!id) return true
  const exact = new Set([
    'filehelper', 'fmessage', 'medianote', 'newsapp', 'notifymessage',
    'weixin', 'brandsessionholder', 'officialaccounts', 'qmessage', 'qqmail',
    'tmessage', 'floatbottle', 'voipnotify', 'voipapp', 'shake',
  ])
  if (exact.has(id)) return true
  if (id.startsWith('gh_')) return true
  if (id.includes('@openim')) return true
  return false
}

export function isOfficialAccount(username) {
  const id = String(username || '').toLowerCase()
  // 公众号/服务号是 gh_*；@openim 是企业号/客服/营销号，不能一起算公众号。
  return id.startsWith('gh_')
}

export function classifyAccount(username) {
  const id = String(username || '')
  if (id.endsWith('@chatroom')) return 'group'
  if (isSystemAccount(id)) return 'other'
  return 'person'
}
