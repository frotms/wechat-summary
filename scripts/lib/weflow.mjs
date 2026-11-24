import { spawnSync } from 'node:child_process'
import { existsSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { isAbsolute, join, resolve } from 'node:path'
import { ExportError, PrereqError, UsageError } from './errors.mjs'

const NODE_SCRIPT = /\.(?:cjs|mjs|js)$/i
const SHELL_SCRIPT = /\.(?:cmd|bat)$/i

function isFile(path) {
  try {
    return statSync(path).isFile()
  } catch {
    return false
  }
}

function firstLine(text) {
  return String(text || '').split(/\r?\n/).map((line) => line.trim()).find(Boolean) || ''
}

function commandPath(name) {
  const finder = process.platform === 'win32' ? 'where' : 'which'
  const res = spawnSync(finder, [name], { encoding: 'utf8', windowsHide: true, timeout: 10_000 })
  if (res.status !== 0) return null
  const lines = String(res.stdout || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  if (process.platform === 'win32') {
    return lines.find((line) => /\.(?:cmd|exe)$/i.test(line)) || lines[0] || null
  }
  return lines[0] || null
}

function npmGlobalRoot() {
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  const res = spawnSync(npm, ['root', '-g'], {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 20_000,
    shell: process.platform === 'win32',
  })
  if (res.status === 0) {
    const root = firstLine(res.stdout)
    if (root) return root
  }
  return null
}

function commonGlobalRoots() {
  const roots = []
  const add = (path) => {
    if (path) roots.push(path)
  }
  add(process.env.npm_config_prefix ? join(process.env.npm_config_prefix, 'node_modules') : '')
  add(process.env.APPDATA ? join(process.env.APPDATA, 'npm', 'node_modules') : '')
  add(process.env.ProgramFiles ? join(process.env.ProgramFiles, 'nodejs', 'node_modules') : '')
  add(join(homedir(), 'AppData', 'Roaming', 'npm', 'node_modules'))
  add('/usr/local/lib/node_modules')
  add('/usr/lib/node_modules')
  add(join(homedir(), '.npm-global', 'lib', 'node_modules'))
  return roots
}

function candidateFromPath(path) {
  if (!path) return null
  const absolute = resolve(path)
  if (!existsSync(absolute)) return null
  if (NODE_SCRIPT.test(absolute)) {
    return { kind: 'node', command: process.execPath, prefix: [absolute], display: absolute }
  }
  if (SHELL_SCRIPT.test(absolute)) {
    return { kind: 'shellFile', command: absolute, prefix: [], display: absolute }
  }
  return { kind: 'file', command: absolute, prefix: [], display: absolute }
}

/**
 * Locate `weflow-cli` without relying on shell quoting for the common case.
 * Preferred forms:
 *   1. `--weflow C:\\...\\weflow-cli\\cli.cjs`  -> node <cli.cjs>
 *   2. npm global install                       -> node <npm root>/weflow-cli/cli.cjs
 *   3. `weflow-cli` on PATH                     -> shell fallback (quoted args)
 */
export function resolveWeFlow(explicit) {
  const explicitCandidates = [explicit, process.env.WEFLOW_CLI].filter(Boolean)
  for (const value of explicitCandidates) {
    const direct = candidateFromPath(value)
    if (direct) return direct
    const found = commandPath(value)
    if (found) {
      const candidate = candidateFromPath(found)
      if (candidate) return candidate
    }
    if (!existsSync(String(value))) {
      throw new PrereqError(`无法定位 weflow-cli: ${value}`, {
        code: 'WEFLOW_NOT_FOUND',
        hints: [
          '先安装：npm install -g weflow-cli',
          '或者用 --weflow 指向 weflow-cli 的 cli.cjs（npm root -g 下）',
        ],
      })
    }
  }

  const roots = []
  const npmRoot = npmGlobalRoot()
  if (npmRoot) roots.push(npmRoot)
  roots.push(...commonGlobalRoots())
  for (const root of roots) {
    const path = join(root, 'weflow-cli', 'cli.cjs')
    if (isFile(path)) {
      return { kind: 'node', command: process.execPath, prefix: [path], display: path }
    }
  }

  const onPath = commandPath(process.platform === 'win32' ? 'weflow-cli.cmd' : 'weflow-cli')
    || commandPath('weflow-cli')
  if (onPath) {
    const candidate = candidateFromPath(onPath)
    if (candidate) return candidate
  }

  throw new PrereqError('没有找到 weflow-cli', {
    code: 'WEFLOW_NOT_FOUND',
    hints: [
      'npm install -g weflow-cli',
      'weflow-cli setup --yes',
      'weflow-cli init',
      '如果装在非标准位置，用 --weflow <cli.cjs 路径> 指定',
    ],
  })
}

function quoteShellArg(arg) {
  const text = String(arg)
  if (/[\r\n"%]/.test(text)) {
    throw new UsageError('参数中包含无法安全传给 shell 的字符；请用 --weflow 指向 cli.cjs', {
      code: 'UNSAFE_SHELL_ARG',
    })
  }
  if (process.platform === 'win32') {
    return `"${text}"`
  }
  return `'${text.replace(/'/g, `'\\''`)}'`
}

// weflow-cli ships mutating commands (send/forward/fav/config/...). This skill
// is read-only by contract, so `runWeFlow` refuses anything outside this list.
// Adding a command here is a deliberate scope change, not a convenience.
const READ_ONLY_COMMANDS = new Set(['check', 'sessions', 'contacts', 'media', 'export'])
const READ_ONLY_FLAGS = new Set(['--version', '-v', '--help', '-h'])
const READ_ONLY_EXPORT_FORMATS = new Set(['json', 'md'])

/**
 * Reject a weflow-cli invocation that could write to, forward, edit or delete
 * chat data. Called by `runWeFlow` before spawning, so every call site is
 * covered - including ones added later.
 */
export function assertReadOnlyWeFlowArgs(args) {
  const list = (args || []).map(String)
  const first = list[0] || ''
  if (!first) {
    throw new ExportError('拒绝执行空的 weflow-cli 命令（只读约束）', {
      code: 'WEFLOW_READ_ONLY_VIOLATION',
    })
  }
  if (first.startsWith('-')) {
    if (READ_ONLY_FLAGS.has(first)) return
    throw new ExportError(`拒绝执行 weflow-cli 参数: ${first}（只读约束）`, {
      code: 'WEFLOW_READ_ONLY_VIOLATION',
      hints: ['本 skill 只允许 check/sessions/contacts/media/export'],
    })
  }
  if (!READ_ONLY_COMMANDS.has(first)) {
    throw new ExportError(`拒绝执行 weflow-cli 命令: ${first}（只读约束）`, {
      code: 'WEFLOW_READ_ONLY_VIOLATION',
      hints: ['本 skill 不写入、不转发、不增删改聊天内容；只允许 check/sessions/contacts/media/export'],
    })
  }
  if (first === 'export') {
    const format = String(list[2] || '')
    if (!READ_ONLY_EXPORT_FORMATS.has(format)) {
      throw new ExportError(`拒绝执行 export ${format || '(缺少格式)'}（只读约束）`, {
        code: 'WEFLOW_READ_ONLY_VIOLATION',
        hints: ['只允许 export <会话> json|md'],
      })
    }
  }
}

/**
 * Run weflow-cli. Returns stdout/stderr; throws on non-zero exit. Chat content
 * is never put into the error message (only stderr and a short status line).
 */
export function runWeFlow(ctx, args, options = {}) {
  assertReadOnlyWeFlowArgs(args)
  const timeoutMs = options.timeoutMs ?? 30 * 60 * 1000
  const shell = ctx.kind === 'shell' || ctx.kind === 'shellFile'
  const fullArgs = shell ? args.map(quoteShellArg) : args
  const res = spawnSync(ctx.command, [...ctx.prefix, ...fullArgs], {
    encoding: 'utf8',
    timeout: timeoutMs,
    maxBuffer: 256 * 1024 * 1024,
    windowsHide: true,
    shell,
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
    },
  })

  if (res.error) {
    if (res.error.code === 'ETIMEDOUT') {
      throw new ExportError(`weflow-cli 执行超时（${Math.round(timeoutMs / 60000)} 分钟）`, {
        code: 'WEFLOW_TIMEOUT',
      })
    }
    if (res.error.code === 'ENOENT') {
      throw new PrereqError(`无法执行 weflow-cli（${ctx.display}）`, {
        code: 'WEFLOW_NOT_EXECUTABLE',
        hints: ['确认 npm install -g weflow-cli 已完成；或用 --weflow 指向 cli.cjs'],
      })
    }
    throw new ExportError(`weflow-cli 执行失败: ${res.error.message}`, { code: 'WEFLOW_SPAWN_FAILED' })
  }

  if (res.status !== 0 && !options.allowNonZero) {
    const stderr = String(res.stderr || '').trim()
    const tail = stderr ? `\n${stderr.slice(-1200)}` : ''
    throw new ExportError(`weflow-cli 退出码 ${res.status}${tail}`, {
      code: 'WEFLOW_EXIT_NONZERO',
      details: { status: res.status },
    })
  }

  return { stdout: String(res.stdout || ''), stderr: String(res.stderr || ''), status: res.status }
}

export function parseJsonOutput(stdout) {
  const text = String(stdout || '').replace(/^\uFEFF/, '').trim()
  if (!text) throw new ExportError('weflow-cli 没有输出 JSON', { code: 'WEFLOW_NO_OUTPUT' })
  try {
    return JSON.parse(text)
  } catch {
    // Progress lines can precede a pretty-printed JSON object. Try the last
    // line that begins a JSON value, then parse everything from there.
    const lines = text.split(/\r?\n/)
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      const trimmed = lines[i].trimStart()
      if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) continue
      try {
        return JSON.parse(lines.slice(i).join('\n').trim())
      } catch {
        // Keep scanning upward.
      }
    }
  }
  throw new ExportError('无法解析 weflow-cli 的 JSON 输出', { code: 'WEFLOW_OUTPUT_UNPARSEABLE' })
}

export function runJson(ctx, args, options = {}) {
  const withJson = args.includes('--json') ? args : [...args, '--json']
  const res = runWeFlow(ctx, withJson, options)
  const data = parseJsonOutput(res.stdout)
  if (options.requireSuccess !== false && data && data.success === false) {
    throw new ExportError(data.error || 'weflow-cli 返回失败', {
      code: data.code || 'WEFLOW_FAILED',
      details: data,
    })
  }
  return data
}

export function getVersion(ctx) {
  try {
    const res = runWeFlow(ctx, ['--version'], { timeoutMs: 20_000 })
    return firstLine(res.stdout) || null
  } catch {
    return null
  }
}

export function checkEnvironment(ctx) {
  try {
    const res = runWeFlow(ctx, ['check', '--json'], { timeoutMs: 120_000 })
    return parseJsonOutput(res.stdout)
  } catch {
    return null
  }
}

export function listSessions(ctx, limit = 1000) {
  const data = runJson(ctx, ['sessions', '--json', '--limit', String(limit)])
  return Array.isArray(data.sessions) ? data.sessions : []
}

export function listContacts(ctx, limit = 5000) {
  const data = runJson(ctx, ['contacts', '--json', '--limit', String(limit)])
  return Array.isArray(data.contacts) ? data.contacts : []
}

export function listVideoMedia(ctx, limit = 5000) {
  const data = runJson(ctx, ['media', '--type', 'video', '--json', '--limit', String(limit)], {
    requireSuccess: false,
    timeoutMs: 300_000,
  })
  if (!data || data.success === false) return []
  return Array.isArray(data.rows) ? data.rows : []
}

/**
 * Run `export ... json` and return the parsed result. `success:false` is
 * returned to the caller (an empty conversation is a normal case), not thrown.
 */
export function exportJson(ctx, options) {
  const args = [
    'export', options.talker, 'json',
    '--contract', 'weflow-v1',
    '--output', options.outputDir,
    '--limit', String(options.limit ?? 0),
    '--non-interactive',
    '--json',
  ]
  if (options.dateArg) args.push('--date', options.dateArg)
  if (options.fromArg) args.push('--from', options.fromArg)
  if (options.toArg) args.push('--to', options.toArg)
  if (options.media) args.push('--media')

  const res = runWeFlow(ctx, args, { timeoutMs: options.timeoutMs, allowNonZero: true })
  try {
    return parseJsonOutput(res.stdout)
  } catch (error) {
    if (res.status !== 0) {
      const stderr = String(res.stderr || '').trim()
      throw new ExportError(`weflow-cli 导出失败（退出码 ${res.status}）${stderr ? `\n${stderr.slice(-1200)}` : ''}`, {
        code: 'JSON_EXPORT_FAILED',
      })
    }
    throw error
  }
}

/**
 * Extract images/files into `options.outputDir` and return the CLI result.
 *
 * We intentionally use `export ... json --media` instead of `export ... md`:
 * weflow-cli 1.21.17's `md` path converts `--date` into internal from/to and
 * then rejects its own range ("markdown 导出暂不支持 --from/--to"). The json
 * `--media` path calls extractMedia with from/to unset, so `--date` works.
 * The generated JSON file is deleted; only the assets + manifest are kept.
 */
export async function exportAssets(ctx, options) {
  const args = [
    'export', options.talker, 'json',
    '--media',
    '--contract', 'weflow-v1',
    '--output', options.outputDir,
    '--limit', String(options.limit ?? 0),
    '--non-interactive',
    '--json',
  ]
  if (options.dateArg) args.push('--date', options.dateArg)
  if (options.fullImages) args.push('--full-images')

  const res = runWeFlow(ctx, args, { timeoutMs: options.timeoutMs, allowNonZero: true })
  let data
  try {
    data = parseJsonOutput(res.stdout)
  } catch (error) {
    if (res.status !== 0) {
      const stderr = String(res.stderr || '').trim()
      throw new ExportError(`weflow-cli 媒体导出失败（退出码 ${res.status}）${stderr ? `\n${stderr.slice(-1200)}` : ''}`, {
        code: 'MEDIA_EXPORT_FAILED',
      })
    }
    throw error
  }
  if (data?.success && data.path) {
    const { unlinkQuiet } = await import('./media.mjs')
    const jsonPath = isAbsolute(data.path) ? data.path : resolve(options.outputDir, data.path)
    await unlinkQuiet(jsonPath)
  }
  return data
}

export function describeWeFlow(ctx) {
  return ctx?.display || ''
}
