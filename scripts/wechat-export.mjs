#!/usr/bin/env node
/**
 * wechat-export — export local WeChat chat history by group/person/time,
 * including text, images, transferred files and videos.
 *
 * The decrypt/read layer is weflow-cli. This script adds:
 *   - target selection (group/person, batch)
 *   - time windows and type filters
 *   - image/file extraction for ranges (weflow-cli md only supports --date)
 *   - video body export (weflow-cli only writes a video label)
 *   - one self-contained bundle: HTML + Markdown + JSONL + media/
 *
 * Only the user's own, already-logged-in WeChat data on this machine is read.
 */
import { promises as fs } from 'node:fs'
import { existsSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { homedir } from 'node:os'
import { isAbsolute, join, relative, resolve } from 'node:path'
import { parseArgv } from './lib/args.mjs'
import { AppError, ExportError, PartialError, PrereqError, UsageError } from './lib/errors.mjs'
import {
  eachDate,
  isWithinWindow,
  localDateOf,
  nowStamp,
  resolveTimeWindow,
  toLocalDateTime,
} from './lib/time.mjs'
import {
  checkEnvironment,
  describeWeFlow,
  exportAssets,
  exportJson,
  getVersion,
  listContacts,
  listSessions,
  listVideoMedia,
  resolveWeFlow,
} from './lib/weflow.mjs'
import {
  classifyAccount,
  deriveWeChatRoots,
  expandHome,
  findVideoRoots,
  isPathInside,
  isOfficialAccount,
  readWeFlowConfig,
  weflowConfigPath,
} from './lib/config.mjs'
import { buildContactMap, isMediaMessage } from './lib/parse.mjs'
import {
  buildVideoIndex,
  buildVideoIndexFromRows,
  parseVideoMd5Candidates,
} from './lib/video.mjs'
import { buildTargetBundle } from './lib/bundle.mjs'
import { renderBatchIndex } from './lib/html.mjs'
import {
  ensureDir,
  pathExists,
  pathToPosix,
  readJson,
  rmQuiet,
  safeName,
  writeJson,
  writeText,
} from './lib/media.mjs'

const SKILL_VERSION = '1.10.3'

const ALL_TYPES = [
  'text', 'image', 'file', 'video', 'voice', 'emoji',
  'location', 'link', 'history', 'card', 'call', 'system', 'quote', 'other',
]

const TYPE_ALIASES = {
  text: 'text', txt: 'text', 文字: 'text', 文本: 'text',
  image: 'image', images: 'image', img: 'image', 图片: 'image', 照片: 'image',
  file: 'file', files: 'file', 文件: 'file', 文档: 'file',
  video: 'video', videos: 'video', 视频: 'video',
  voice: 'voice', audio: 'voice', 语音: 'voice',
  emoji: 'emoji', 表情: 'emoji',
  location: 'location', 位置: 'location',
  link: 'link', 链接: 'link',
  history: 'history', chat_history: 'history', 聊天记录: 'history', 转发: 'history',
  card: 'card', 卡片: 'card',
  call: 'call', 通话: 'call',
  system: 'system', 系统: 'system',
  quote: 'quote', 引用: 'quote',
  other: 'other', 其他: 'other',
  all: 'all', 全部: 'all', '*': 'all',
}

function parseTypes(value) {
  const text = String(value ?? 'text,image,file,video,voice,emoji,link,history').trim()
  if (!text) throw new UsageError('--types 不能为空', { code: 'INVALID_TYPES' })
  const selected = new Set()
  for (const raw of text.split(/[\s,，、]+/).filter(Boolean)) {
    const key = raw.toLowerCase()
    const mapped = TYPE_ALIASES[key]
    if (!mapped) {
      throw new UsageError(`未知消息类型: ${raw}`, {
        code: 'INVALID_TYPES',
        hints: [`可用: ${ALL_TYPES.join(', ')}, all`],
      })
    }
    if (mapped === 'all') {
      for (const type of ALL_TYPES) selected.add(type)
    } else {
      selected.add(mapped)
    }
  }
  if (!selected.size) throw new UsageError('--types 没有选出任何类型', { code: 'INVALID_TYPES' })
  return selected
}

function numberOption(opts, name, fallback, min, max) {
  if (opts[name] === undefined) return fallback
  const value = Number(opts[name])
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new UsageError(`--${name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)} 必须是 ${min}..${max} 的整数`, {
      code: 'INVALID_NUMBER',
    })
  }
  return value
}

function normalizeTimestamp(value) {
  const n = Number(value) || 0
  if (n <= 0) return 0
  return n > 10_000_000_000 ? Math.floor(n / 1000) : Math.floor(n)
}

function printUsage() {
  console.log(`wechat-export ${SKILL_VERSION}

导出本机微信聊天记录（文字、图片、文件、视频），按群 / 按个人 / 按时间筛选。

用法:
  node scripts/wechat-export.mjs [选项]

目标选择:
  --list                      列出会话（群和最近联系过的人）
  --list-contacts             同时列出不在最近会话里的联系人
  --include-official          列表/批量时包含公众号与系统账号
  --include-summary           列表 JSON 里包含最后一条消息摘要（默认不含）
  --target <名称或ID>         导出指定会话，可重复
  --group <名称或ID>          只按群名/群ID匹配，可重复
  --person <名称或ID>         只按人名/ID匹配，可重复
  --all-groups                导出所有群会话
  --all-persons               导出所有个人会话
  --all-official              导出所有公众号会话（gh_）
  --keyword <文字>            过滤 --all-* / --list 的目标
  --max-targets <n>           批量导出的目标上限（默认 all=50，显式=200，硬上限 500）
  --include-empty             保留没有消息的会话（默认跳过 lastTimestamp=0 的会话）

时间:
  --from <YYYY-MM-DD|ISO>     起始时间（含当天）
  --to <YYYY-MM-DD|ISO>       结束时间（含当天）
  --date <YYYY-MM-DD>         只导出一天
  --days <n>                  最近 n 个自然日（含今天）
  --all-time                  全部时间（不写时间参数时的默认）
  --limit <n>                 每个会话最多 n 条消息（0=全部）

类型:
  --types <列表>              text,image,file,video,voice,emoji,link,history,...,all
                              默认 text,image,file,video,voice,emoji,link,history

媒体:
  --media-strategy <策略>     auto | day | full | none（默认 auto）
                              auto: 范围 <=31 天按有媒体的日期逐日抽取；更宽则全量
                              day:  按天逐次抽取（范围很宽时会慢）
                              full: 一次抽取全部历史媒体
                              none: 只导出消息，不抽取图片/文件
  --full-images               使用原图而不是微信缓存缩略图（慢数倍）
  --no-media                  等同 --media-strategy none
  --no-video-scan             不扫描 msg/video，视频全部标记缺失
  --video-root <目录>         额外指定微信账号目录或视频目录，可重复
  --weflow-config <文件>      weflow-cli 配置文件路径（默认 ~/.weflow-cli/config.json）
  --media-map <文件>          4.2 媒体映射（wechat42-media-map.py 产物；补视频/图片）
  --revoke-map <文件>         撤回映射（wechat-revoke-map.py 产物；标记已撤回消息）
  --prune-raw                 成包后删除 raw/ 中间目录
  --keep-raw                  保留 raw/（默认）

输出:
  --output <目录>             默认 ~/.wechat-export/<时间戳>
  --force                     输出目录非空时，先改名为 .bak-<时间戳>
  --no-html                   不生成 index.html
  --no-markdown               不生成 transcript.md
  --max-html-messages <n>     HTML 最多包含多少条（默认 50000；超过则跳过 HTML）
  --json                      在 stdout 输出机器可读结果
  --dry-run                   只打印计划，不写文件（仍会读会话列表解析目标）
  --allow-partial             部分目标/媒体失败时继续
  --check                     只做环境体检
  --timeout-minutes <n>       单次 weflow-cli 调用超时（默认 30）
  --weflow <命令|cli.cjs>     指定 weflow-cli 位置
  --me-label <文字>           自己消息的显示名（默认“我”）
  --include-raw               在 JSONL 中包含原始 XML（调试用，体积大）
  -h, --help                  显示帮助
  -v, --version               显示版本

示例:
  node scripts/wechat-export.mjs --list
  node scripts/wechat-export.mjs --group "项目群" --days 7
  node scripts/wechat-export.mjs --person "张三" --from 2026-01-01 --to 2026-01-31
  node scripts/wechat-export.mjs --all-groups --days 3 --types text,video
  node scripts/wechat-export.mjs --target "项目群" --types all --output "D:\\\\exports\\\\项目群"
`)
}

function log(message) {
  console.error(message)
}

function progress(opts, message) {
  console.error(message)
}

function jsonResult(payload) {
  process.stdout.write(JSON.stringify(payload, null, 2) + '\n')
}

function handleError(error, opts) {
  const appError = error instanceof AppError
    ? error
    : new AppError(error?.message || String(error), { code: 'UNEXPECTED' })
  if (opts?.json) {
    jsonResult({
      success: false,
      code: appError.code,
      error: appError.message,
      hints: appError.hints,
      details: appError.details,
    })
  } else {
    log(`\n✗ ${appError.message}`)
    for (const hint of appError.hints || []) log(`  ${hint}`)
  }
  process.exitCode = appError.exitCode
}

function checkReadiness(check) {
  if (!check) return
  const configuration = check.configuration || {}
  if (configuration.initialized === false) {
    throw new PrereqError('weflow-cli 还没有初始化（读不到微信数据库）', {
      code: 'NOT_INITIALIZED',
      hints: [
        '先启动并登录微信桌面客户端',
        '在终端运行：weflow-cli init',
        '然后运行：weflow-cli check',
      ],
    })
  }
  if (configuration.messageDatabase === false) {
    throw new PrereqError('weflow-cli 找不到微信消息数据库', {
      code: 'MESSAGE_DB_MISSING',
      hints: [
        '确认微信已登录，并且数据目录是默认位置',
        '运行：weflow-cli init --path "<微信数据目录>"',
        '或：weflow-cli init --search-drives',
      ],
    })
  }
}

function pythonReady(check) {
  if (!check) return true
  const python = check.runtime?.python
  if (python && python.available === false) return false
  const required = check.dependencies?.required
  if (required && typeof required === 'object') {
    return Object.values(required).every((value) => value === true)
  }
  return true
}

async function runCheck(opts) {
  const ctx = resolveWeFlow(opts.weflow)
  const version = getVersion(ctx)
  const check = checkEnvironment(ctx)
  const config = readWeFlowConfig(opts.weflowConfig)
  const roots = deriveWeChatRoots(config, {
    explicitRoots: opts.videoRoot || [],
    checkDataDirs: check?.wechatClient?.dataDirs || [],
  })
  const videoRoots = findVideoRoots(roots)
  const videoIndex = buildVideoIndex(videoRoots)
  const python = check?.runtime?.python
  const required = check?.dependencies?.required || null
  const missingDeps = required
    ? Object.entries(required).filter(([, ok]) => ok !== true).map(([name]) => name)
    : []
  const ready = Boolean(
    check
    && check.configuration?.initialized !== false
    && check.configuration?.messageDatabase !== false,
  )
  const mediaReady = python ? python.available !== false && missingDeps.length === 0 : true

  const payload = {
    success: true,
    ready,
    mediaReady,
    weflow: {
      found: true,
      path: describeWeFlow(ctx),
      version,
    },
    check: check ? {
      schema: check.schema || null,
      initialized: check.configuration?.initialized ?? null,
      messageDatabase: check.configuration?.messageDatabase ?? null,
      python: python ? { available: python.available === true, version: python.version || '' } : null,
      missingRequiredDependencies: missingDeps,
      wechatRunning: check.wechatClient?.running ?? null,
      wechatVersion: check.wechatClient?.version ?? null,
      wechatCompatibility: check.wechatClient?.compatibility ?? null,
    } : null,
    config: {
      path: config.path || weflowConfigPath(),
      exists: config.exists,
      hasNtDbPath: Boolean(config.ntDbPath),
      hasDbPath: Boolean(config.dbPath || config.dbPath3x),
    },
    wechat: {
      accountRoots: roots,
      videoRoots,
      videoFiles: videoIndex.files.length,
    },
    hints: [],
  }

  if (!ready) {
    payload.hints.push('启动并登录微信，然后运行 weflow-cli init')
  }
  if (!mediaReady) {
    payload.hints.push('运行 weflow-cli setup --yes 安装 Python 依赖（图片/文件抽取需要）')
  }
  if (!videoRoots.length) {
    payload.hints.push('没有找到 msg/video 目录；视频会标记缺失。可用 --video-root 指定账号目录')
  }

  if (opts.json) {
    jsonResult(payload)
  } else {
    log(`weflow-cli: ${version || '未知版本'}`)
    log(`  路径: ${payload.weflow.path}`)
    log(`初始化: ${payload.check?.initialized ? '✓' : '✗'}`)
    log(`消息库: ${payload.check?.messageDatabase ? '✓' : '✗'}`)
    log(`Python: ${python ? (python.available ? `✓ ${python.version || ''}` : '✗ 未找到') : '未知'}`)
    if (missingDeps.length) log(`缺失依赖: ${missingDeps.join(', ')}`)
    log(`微信客户端: ${payload.check?.wechatRunning ? '运行中' : '未运行（仍可读本地库）'}`
      + `${payload.check?.wechatVersion ? ` ${payload.check.wechatVersion}` : ''}`)
    if (payload.check?.wechatCompatibility?.verdict) {
      log(`  兼容性: ${payload.check.wechatCompatibility.verdict}`
        + `（weflow-cli 验证到 ${payload.check.wechatCompatibility.verifiedVersion || '未知'}）`)
    }
    log(`账号根目录: ${roots.length}`)
    for (const root of roots) log(`  ${root}`)
    log(`视频目录: ${videoRoots.length}，视频文件: ${videoIndex.files.length}`)
    for (const root of videoRoots) log(`  ${root}`)
    if (payload.hints.length) {
      log('')
      for (const hint of payload.hints) log(`建议: ${hint}`)
    }
  }
  process.exitCode = ready ? 0 : 3
}

function normalizeSession(session) {
  const id = String(session?.username || '').trim()
  const kind = classifyAccount(id)
  const name = String(session?.displayName || id).trim() || id
  const aliases = [...new Set([
    session?.displayName,
    session?.remark,
    session?.nickname,
  ].filter(Boolean).map(String))]
  return {
    id,
    name,
    aliases,
    kind,
    isOfficial: isOfficialAccount(id),
    lastTimestamp: normalizeTimestamp(session?.lastTimestamp || session?.sortTimestamp || 0),
    unreadCount: Number(session?.unreadCount) || 0,
    source: 'session',
    summary: String(session?.summary || ''),
  }
}

function normalizeContact(contact) {
  const id = String(contact?.username || '').trim()
  const kind = classifyAccount(id)
  const name = String(
    contact?.remark || contact?.displayName || contact?.nickname || contact?.alias || id,
  ).trim() || id
  const aliases = [...new Set([
    contact?.remark,
    contact?.displayName,
    contact?.nickname,
    contact?.alias,
  ].filter(Boolean).map(String))]
  return {
    id,
    name,
    aliases,
    kind,
    isOfficial: isOfficialAccount(id),
    lastTimestamp: 0,
    unreadCount: 0,
    source: 'contact',
    summary: '',
  }
}

function buildTargetPool(sessions, contacts, opts) {
  const pool = new Map()
  for (const session of sessions || []) {
    const item = normalizeSession(session)
    if (!item.id) continue
    pool.set(item.id, item)
  }
  const explicit = (opts.target || []).length > 0 || (opts.group || []).length > 0 || (opts.person || []).length > 0
  for (const contact of contacts || []) {
    const item = normalizeContact(contact)
    if (!item.id) continue
    const existing = pool.get(item.id)
    if (existing) {
      // Prefer the contact remark for matching: users usually type the remark,
      // not the session's display name.
      if (contact?.remark) existing.name = item.name
      else if (!existing.name || existing.name === existing.id) existing.name = item.name
      existing.aliases = [...new Set([...(existing.aliases || []), ...(item.aliases || [])])]
      continue
    }
    if (opts.listContacts || explicit) pool.set(item.id, item)
  }
  return [...pool.values()]
}

function targetLabel(target) {
  const kind = target.kind === 'group' ? '群' : target.kind === 'person' ? '个人' : '其他'
  return `${target.name}（${kind}，${target.id}）`
}

function targetDirName(target, used) {
  const base = safeName(target.name || target.id, 'target', 56)
  const suffix = createHash('sha1').update(target.id).digest('hex').slice(0, 8)
  let name = `${base}_${suffix}`
  for (let index = 2; used.has(name); index += 1) name = `${base}_${suffix}_${index}`
  used.add(name)
  return name
}

function resolveOne(input, pool, options = {}) {
  const query = String(input || '').trim()
  if (!query) throw new UsageError('目标名称不能为空', { code: 'EMPTY_TARGET' })
  const kindFilter = options.kind || null
  let candidates = pool
  if (kindFilter) candidates = candidates.filter((item) => item.kind === kindFilter)
  if (!candidates.length) {
    throw new UsageError(`没有可匹配的${kindFilter === 'group' ? '群' : kindFilter === 'person' ? '个人' : ''}会话`, {
      code: 'TARGET_NOT_FOUND',
      hints: ['先运行：node scripts/wechat-export.mjs --list'],
    })
  }

  const lower = query.toLowerCase()
  let matches = candidates.filter((item) => item.id === query)
  if (!matches.length) matches = candidates.filter((item) => item.id.toLowerCase() === lower)
  if (!matches.length) matches = candidates.filter((item) => item.name === query)
  if (!matches.length) matches = candidates.filter((item) => item.name.toLowerCase() === lower)
  if (!matches.length) matches = candidates.filter((item) => (item.aliases || []).some((alias) => alias === query))
  if (!matches.length) matches = candidates.filter((item) => (item.aliases || []).some((alias) => alias.toLowerCase() === lower))
  if (!matches.length) {
    matches = candidates.filter((item) => item.id.toLowerCase().includes(lower)
      || item.name.toLowerCase().includes(lower)
      || (item.aliases || []).some((alias) => alias.toLowerCase().includes(lower)))
  }

  if (matches.length === 1) return matches[0]
  if (matches.length > 1) {
    const unique = [...new Map(matches.map((item) => [item.id, item])).values()]
    if (unique.length === 1) return unique[0]
    throw new UsageError(`“${input}”匹配到 ${unique.length} 个会话，无法确定是哪一个`, {
      code: 'AMBIGUOUS_TARGET',
      hints: unique.slice(0, 8).map((item) => `  ${targetLabel(item)}`),
    })
  }

  const related = candidates
    .filter((item) => item.name.includes(query)
      || query.includes(item.name)
      || item.id.includes(query)
      || (item.aliases || []).some((alias) => alias.includes(query) || query.includes(alias)))
    .slice(0, 6)
  throw new UsageError(`找不到会话: ${input}`, {
    code: 'TARGET_NOT_FOUND',
    hints: [
      ...related.map((item) => `  相近: ${targetLabel(item)}`),
      '先运行：node scripts/wechat-export.mjs --list',
    ],
  })
}

function resolveTargets(opts, pool, warnings) {
  const targets = []
  const seen = new Set()
  const add = (target) => {
    if (!target || seen.has(target.id)) return
    seen.add(target.id)
    targets.push(target)
  }

  for (const name of opts.target || []) add(resolveOne(name, pool))
  for (const name of opts.group || []) add(resolveOne(name, pool, { kind: 'group' }))
  for (const name of opts.person || []) add(resolveOne(name, pool, { kind: 'person' }))

  const keyword = String(opts.keyword || '').trim().toLowerCase()
  const byKeyword = (item) => !keyword
    || item.name.toLowerCase().includes(keyword)
    || item.id.toLowerCase().includes(keyword)

  if (opts.allGroups) {
    for (const item of pool
      .filter((entry) => entry.kind === 'group' && byKeyword(entry))
      .filter((entry) => opts.includeEmpty || entry.lastTimestamp > 0)
      .sort((a, b) => b.lastTimestamp - a.lastTimestamp)) {
      add(item)
    }
  }
  if (opts.allPersons) {
    for (const item of pool
      .filter((entry) => entry.kind === 'person' && byKeyword(entry) && entry.source === 'session')
      .filter((entry) => opts.includeEmpty || entry.lastTimestamp > 0)
      .sort((a, b) => b.lastTimestamp - a.lastTimestamp)) {
      add(item)
    }
  }
  if (opts.allOfficial) {
    for (const item of pool
      .filter((entry) => entry.isOfficial && byKeyword(entry))
      .filter((entry) => opts.includeEmpty || entry.lastTimestamp > 0)
      .sort((a, b) => b.lastTimestamp - a.lastTimestamp)) {
      add(item)
    }
  }

  const isBatch = Boolean(opts.allGroups || opts.allPersons || opts.allOfficial)
  const max = opts.maxTargets === undefined ? (isBatch ? 50 : 200) : Number(opts.maxTargets)
  if (targets.length > max) {
    warnings.push(`目标数 ${targets.length} 超过上限 ${max}，只导出前 ${max} 个（可用 --max-targets 调整）`)
    targets.length = max
  }
  if (!targets.length) {
    const hints = [
      '先运行：node scripts/wechat-export.mjs --list',
      '然后用 --group "群名" / --person "人名" / --target "名称或ID"',
      '或用 --all-groups / --all-persons 批量导出',
    ]
    if (opts.allOfficial) {
      hints.unshift('没有匹配的公众号会话（gh_*）；企业号/客服号（@openim）不算公众号，可用 --target 单独导出')
    }
    throw new UsageError('没有指定要导出的会话', { code: 'NO_TARGET', hints })
  }
  return targets
}

async function runList(opts) {
  const ctx = resolveWeFlow(opts.weflow)
  checkReadiness(checkEnvironment(ctx))
  const sessions = listSessions(ctx, 1000)
  let contacts = []
  if (opts.listContacts) {
    try {
      contacts = listContacts(ctx, 5000)
    } catch (error) {
      log(`（联系人列表读取失败，只显示最近会话：${error.message}）`)
    }
  }
  let pool = buildTargetPool(sessions, contacts, { ...opts, target: opts.target || [] })
  if (!opts.includeOfficial) pool = pool.filter((item) => item.kind !== 'other')
  const keyword = String(opts.keyword || '').trim().toLowerCase()
  if (keyword) {
    pool = pool.filter((item) => item.name.toLowerCase().includes(keyword) || item.id.toLowerCase().includes(keyword))
  }
  pool = pool.sort((a, b) => b.lastTimestamp - a.lastTimestamp)

  if (opts.json) {
    jsonResult({
      success: true,
      count: pool.length,
      sessions: pool.map((item, index) => ({
        index: index + 1,
        id: item.id,
        name: item.name,
        kind: item.kind,
        lastTimestamp: item.lastTimestamp || null,
        lastMessageTime: item.lastTimestamp ? toLocalDateTime(item.lastTimestamp) : null,
        unreadCount: item.unreadCount,
        source: item.source,
        ...(opts.includeSummary ? { summary: item.summary } : {}),
      })),
    })
    return
  }

  log(`会话列表（${pool.length} 条）\n`)
  log('序号  类型  会话ID                          名称                    最后消息')
  log('─'.repeat(100))
  pool.forEach((item, index) => {
    const num = String(index + 1).padStart(3)
    const kind = item.kind === 'group' ? '群  ' : item.kind === 'person' ? '个人' : '其他'
    const id = item.id.padEnd(30)
    const name = item.name.slice(0, 20).padEnd(22)
    const time = item.lastTimestamp ? toLocalDateTime(item.lastTimestamp) : '-'
    log(`${num}  ${kind}  ${id} ${name} ${time}`)
  })
  log('')
  log('用它拿到准确名称/ID 后，再执行导出。')
}

function defaultOutputDir() {
  return join(homedir(), '.wechat-export', nowStamp())
}

function findGitRoot(start) {
  let current = resolve(start)
  for (let depth = 0; depth < 8; depth += 1) {
    if (existsSync(join(current, '.git'))) return current
    const parent = resolve(current, '..')
    if (parent === current) break
    current = parent
  }
  return null
}

async function resolveOutputDir(opts, wechatRoots, warnings) {
  const output = opts.output ? resolve(expandHome(opts.output)) : defaultOutputDir()
  for (const root of wechatRoots) {
    if (isPathInside(root, output)) {
      throw new UsageError('输出目录不能放在微信数据目录内（源数据只读保护）', {
        code: 'OUTPUT_INSIDE_WECHAT',
        hints: ['换一个目录：--output "D:\\\\wechat-exports\\\\..."'],
      })
    }
  }

  if (await pathExists(output)) {
    let stat
    try {
      stat = await fs.stat(output)
    } catch {
      stat = null
    }
    if (!stat?.isDirectory()) {
      throw new UsageError(`输出路径已存在且不是目录: ${output}`, { code: 'OUTPUT_NOT_DIRECTORY' })
    }
    const entries = await fs.readdir(output).catch(() => [])
    if (entries.length) {
      if (!opts.force) {
        throw new UsageError(`输出目录非空: ${output}`, {
          code: 'OUTPUT_NOT_EMPTY',
          hints: ['换一个 --output，或加 --force（旧目录会改名为 .bak-<时间戳>）'],
        })
      }
      const backup = `${output}.bak-${nowStamp()}`
      await fs.rename(output, backup)
      warnings.push(`输出目录非空，旧目录已改名: ${backup}`)
    }
  }

  await ensureDir(output)
  try {
    await fs.chmod(output, 0o700)
  } catch {
    // Windows ignores mode; best effort.
  }
  const gitRoot = findGitRoot(output)
  if (gitRoot) {
    warnings.push(`输出目录在 Git 仓库内（${gitRoot}），注意不要把聊天记录提交上去`)
  }
  return output
}

async function readRawMessages(jsonPath) {
  const data = await readJson(jsonPath)
  if (Array.isArray(data)) return { messages: data, coverage: null }
  if (Array.isArray(data?.messages)) return { messages: data.messages, coverage: data.coverage || null }
  throw new ExportError('weflow-cli 的 JSON 导出格式无法识别（没有 messages 数组）', {
    code: 'RAW_MESSAGES_INVALID',
  })
}

async function readMediaManifest(rawDir, talker) {
  // Assets may come from the old single manifest or from per-day run dirs
  // (`_asset-runs/<date>/<talker>_assets/manifest.json`). Merge them all and
  // rewrite each path so it is relative to rawDir.
  const candidates = []
  const mainManifest = join(rawDir, `${talker}_assets`, 'manifest.json')
  if (await pathExists(mainManifest)) candidates.push({ path: mainManifest, base: rawDir })
  const runsDir = join(rawDir, '_asset-runs')
  if (await pathExists(runsDir)) {
    let entries = []
    try {
      entries = await fs.readdir(runsDir, { withFileTypes: true })
    } catch {
      entries = []
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue
      const base = join(runsDir, entry.name)
      const manifest = join(base, `${talker}_assets`, 'manifest.json')
      if (await pathExists(manifest)) candidates.push({ path: manifest, base })
    }
  }
  if (!candidates.length) return { records: {}, documents: {}, path: null }

  const records = {}
  let documents = {}
  let raw = null
  for (const candidate of candidates) {
    let data
    try {
      data = await readJson(candidate.path)
    } catch {
      continue
    }
    raw = data
    documents = { ...documents, ...(data?.documents || {}) }
    const source = data?.messages && typeof data.messages === 'object' ? data.messages : data
    const prefix = pathToPosix(relative(rawDir, candidate.base))
    for (const [key, value] of Object.entries(source || {})) {
      let record
      if (typeof value === 'string') record = { path: value, kind: 'image' }
      else if (value && typeof value === 'object' && value.path) record = { ...value }
      if (!record) continue
      record.path = prefix ? `${prefix}/${record.path}` : record.path
      records[key] = record
    }
  }
  return { records, documents, path: candidates[0].path, raw }
}

function mediaDaysOf(rawMessages, time) {
  const range = time.fromDate && time.toDate ? new Set(eachDate(time.fromDate, time.toDate)) : null
  const days = new Set()
  for (const message of rawMessages) {
    if (!isMediaMessage(message)) continue
    if (!(Number(message.createTime) > 0)) continue
    const day = localDateOf(message.createTime)
    if (!day) continue
    if (range && !range.has(day)) continue
    days.add(day)
  }
  return [...days].sort()
}

/**
 * weflow-cli's markdown export supports `--date` but not `--from/--to`.
 * For ranges we run it once per day that actually contains image/file
 * messages, then read the merged manifest. Wide ranges fall back to one
 * full-history extraction, with a warning.
 */
async function extractMediaForTarget({ ctx, target, rawDir, rawMessages, time, opts, warnings }) {
  const mediaDays = mediaDaysOf(rawMessages, time)
  if (!mediaDays.length) {
    return { performed: true, runs: 0, completed: 0, failed: false, error: null, strategyUsed: 'none' }
  }

  const strategy = opts.mediaStrategy
  let runs = []
  let strategyUsed = 'day'

  if (strategy === 'full') {
    runs = [{ dateArg: undefined }]
    strategyUsed = 'full'
    warnings.push(`媒体策略 full：会抽取该会话全部历史图片/文件（不只是本次范围）`)
  } else if (time.kind === 'date') {
    if (mediaDays.includes(time.dateArg)) runs = [{ dateArg: time.dateArg }]
    strategyUsed = 'day'
  } else {
    // mediaDays already only contains image/file messages inside the JSON
    // export's range, so per-day extraction is correct even for an open-ended
    // --from/--to. Wide ranges fall back to one full extraction.
    const perDay = strategy === 'day' || mediaDays.length <= 31
    if (perDay) {
      runs = mediaDays.map((day) => ({ dateArg: day }))
      strategyUsed = 'day'
    } else {
      runs = [{ dateArg: undefined }]
      strategyUsed = 'full'
      warnings.push(`媒体日期 ${mediaDays.length} 天超过自动逐日阈值 31 天，媒体改为全量抽取；可用 --media-strategy day 强制逐日`)
    }
  }

  if (strategy === 'day' && runs.length > 366) {
    runs = [{ dateArg: undefined }]
    strategyUsed = 'full'
    warnings.push('媒体日期超过 366 天，改为全量抽取')
  }

  let completed = 0
  let failed = false
  let error = null
  for (let index = 0; index < runs.length; index += 1) {
    const run = runs[index]
    const label = run.dateArg || '全量'
    progress(opts, `    媒体抽取 ${label} (${index + 1}/${runs.length})`)
    try {
      const runDir = join(rawDir, '_asset-runs', run.dateArg || 'full')
      await ensureDir(runDir)
      const data = await exportAssets(ctx, {
        talker: target.id,
        outputDir: runDir,
        dateArg: run.dateArg,
        limit: 0,
        fullImages: Boolean(opts.fullImages),
        timeoutMs: opts.timeoutMinutes * 60_000,
      })
      if (data?.success === false) {
        const message = String(data.error || '')
        if (message.includes('未找到消息')) continue
        throw new ExportError(`媒体抽取失败: ${message}`, { code: data.code || 'MEDIA_EXPORT_FAILED' })
      }
      completed += 1
    } catch (caught) {
      failed = true
      error = caught.message
      if (!opts.allowPartial) {
        throw caught instanceof AppError
          ? caught
          : new ExportError(`媒体抽取失败: ${caught.message}`, { code: 'MEDIA_EXPORT_FAILED' })
      }
      warnings.push(`媒体抽取失败（${label}）：${caught.message}`)
      break
    }
  }

  return { performed: true, runs: runs.length, completed, failed, error, strategyUsed }
}

async function readVideoIndex({ ctx, opts, check, warnings, videoRequested, wechatRoots }) {
  if (!videoRequested || opts.videoScan === false) return { index: null, roots: [] }
  const videoRoots = findVideoRoots(wechatRoots)
  let index = buildVideoIndex(videoRoots)
  if (!index.files.length) {
    try {
      const rows = listVideoMedia(ctx, 5000)
      if (rows.length) {
        index = buildVideoIndexFromRows(rows)
        warnings.push(`msg/video 目录为空，改用 weflow-cli media 索引（${rows.length} 条）`)
      }
    } catch {
      // The account root could not be resolved and the fallback is unavailable.
    }
  }
  if (!index.files.length) {
    warnings.push('没有找到微信视频文件；视频消息会标记为缺失')
  }
  if (index.truncated) {
    warnings.push('视频目录文件过多，索引已截断（最多 25 万个文件）')
  }
  return { index, roots: videoRoots }
}

function batchTotals(bundles) {
  const totals = {
    targets: bundles.length,
    messages: 0,
    media: {
      images: { found: 0, missing: 0 },
      files: { found: 0, missing: 0 },
      videos: { found: 0, missing: 0, warned: 0 },
      voices: { found: 0, missing: 0 },
      emojis: { found: 0, missing: 0 },
    },
    byType: {},
    revoked: 0,
  }
  for (const bundle of bundles) {
    const stats = bundle.stats || {}
    totals.messages += stats.exportedMessages || 0
    totals.revoked += stats.revoked || 0
    for (const [type, count] of Object.entries(stats.byType || {})) {
      totals.byType[type] = (totals.byType[type] || 0) + count
    }
    for (const key of ['images', 'files', 'videos', 'voices', 'emojis']) {
      totals.media[key].found += stats.media?.[key]?.found || 0
      totals.media[key].missing += stats.media?.[key]?.missing || 0
    }
    totals.media.videos.warned += stats.media?.videos?.warned || 0
  }
  return totals
}

async function writeBatchOutputs({ outputDir, time, types, bundles, failures, warnings, weflowVersion }) {
  const totals = batchTotals(bundles)
  const manifest = {
    schema: 'wechat-export-batch/v1',
    generatedAt: new Date().toISOString(),
    query: {
      label: time.label,
      date: time.dateArg || null,
      from: time.fromArg || null,
      to: time.toArg || null,
      types: [...types],
    },
    source: {
      tool: 'wechat-export (weflow-cli)',
      skillVersion: SKILL_VERSION,
      weflowVersion: weflowVersion || null,
    },
    totals,
    bundles: bundles.map((bundle) => ({
      target: bundle.target,
      dir: bundle.dir,
      stats: bundle.stats,
      html: bundle.html,
      markdown: bundle.markdown,
      manifest: `${bundle.dir}/manifest.json`,
      warnings: bundle.warnings,
    })),
    failures,
    warnings: [...new Set(warnings)],
  }
  await writeJson(join(outputDir, 'manifest.json'), manifest)
  await writeText(join(outputDir, 'index.html'), renderBatchIndex({
    generatedAt: manifest.generatedAt,
    query: manifest.query,
    bundles: manifest.bundles,
  }))
  const lines = [
    '微信聊天记录批量导出',
    '====================',
    '',
    `时间范围：${manifest.query.label}`,
    `导出时间：${manifest.generatedAt}`,
    `会话数：${bundles.length}`,
    `消息数：${totals.messages}`,
    '',
    '打开 index.html 浏览各会话；每个子目录都有独立的 index.html / transcript.md / messages.jsonl / media/。',
    '',
    '隐私提示：本目录包含真实聊天内容与第三方个人信息。不要提交到 Git、网盘或公开链接。',
    '',
  ]
  if (failures.length) {
    lines.push('未完成的会话：')
    for (const failure of failures) lines.push(`- ${failure.target.name || failure.target.id}: ${failure.error}`)
    lines.push('')
  }
  await writeText(join(outputDir, 'README.txt'), lines.join('\n'))
  return manifest
}

function printHumanSummary({ outputDir, manifest, warnings }) {
  const totals = manifest.totals
  log('')
  log('✓ 导出完成')
  log(`  输出: ${outputDir}`)
  log(`  会话: ${totals.targets} 个，消息: ${totals.messages} 条`)
  log(`  图片: 已导出 ${totals.media.images.found}，缺失 ${totals.media.images.missing}`)
  log(`  文件: 已导出 ${totals.media.files.found}，缺失 ${totals.media.files.missing}`)
  log(`  视频: 已导出 ${totals.media.videos.found}，缺失 ${totals.media.videos.missing}`)
  log(`  语音: 已导出 ${totals.media.voices?.found ?? 0}，缺失 ${totals.media.voices?.missing ?? 0}`)
  if (totals.revoked) log(`  已标记撤回: ${totals.revoked} 条（见 revokes.json）`)
  const index = join(outputDir, 'index.html')
  log(`  浏览: ${index}`)
  if (warnings.length) {
    log('')
    log('提醒:')
    for (const warning of [...new Set(warnings)]) log(`  - ${warning}`)
  }
}

async function runExport(opts) {
  const types = parseTypes(opts.types)
  const time = resolveTimeWindow(opts)
  const mediaRequested = types.has('image') || types.has('file')
  const videoRequested = types.has('video')
  const warnings = []

  const ctx = resolveWeFlow(opts.weflow)
  const weflowVersion = getVersion(ctx)
  const check = checkEnvironment(ctx)
  checkReadiness(check)

  const pyReady = pythonReady(check)
  const mediaEnabled = mediaRequested
    && opts.media !== false
    && opts.mediaStrategy !== 'none'
  if (mediaEnabled && !pyReady) {
    if (!opts.allowPartial) {
      throw new PrereqError('图片/文件抽取需要 Python 依赖，但当前环境不满足', {
        code: 'PYTHON_DEPS_MISSING',
        hints: [
          '运行：weflow-cli setup --yes',
          '或：python -m pip install sqlcipher3 html2text zstandard cryptography',
          '只导出文字/视频可加：--types text,video',
          '或者加 --allow-partial 继续，图片/文件会标记为缺失',
        ],
      })
    }
    warnings.push('Python 依赖不满足，图片/文件抽取已跳过，相关消息会标记为缺失')
  }
  if (check?.wechatClient?.running === false) {
    warnings.push('微信客户端未运行；读取的是本地已有数据库，可能不是最新消息')
  }

  const sessions = listSessions(ctx, 1000)
  let contacts = []
  try {
    contacts = listContacts(ctx, 5000)
  } catch (error) {
    warnings.push(`联系人列表读取失败，群成员可能只显示 wxid：${error.message}`)
  }
  const contactMap = buildContactMap(contacts)
  let mediaMap = null
  if (opts.mediaMap) {
    try {
      const data = await readJson(resolve(expandHome(opts.mediaMap)))
      mediaMap = new Map(Object.entries(data?.items || data || {}))
    } catch (error) {
      warnings.push(`媒体映射读取失败（${opts.mediaMap}）：${error.message}`)
    }
  }
  let revokeMap = null
  let revokeReport = null
  if (opts.revokeMap) {
    try {
      const data = await readJson(resolve(expandHome(opts.revokeMap)))
      revokeMap = new Map(Object.entries(data?.items || {}))
      revokeReport = {
        schema: data?.schema || 'wechat-revoke-map/v1',
        items: data?.items || {},
        orphans: Array.isArray(data?.orphans) ? data.orphans : [],
      }
    } catch (error) {
      warnings.push(`撤回映射读取失败（${opts.revokeMap}）：${error.message}`)
    }
  }
  const pool = buildTargetPool(sessions, contacts, opts)
  const targets = resolveTargets(opts, pool, warnings)

  const config = readWeFlowConfig(opts.weflowConfig)
  // Roots derived from the real config protect the source data. Explicit
  // --video-root paths are allowed to be broader (a parent folder, a mounted
  // drive) and must not make the default output directory look "inside".
  const protectedRoots = deriveWeChatRoots(config, {
    checkDataDirs: check?.wechatClient?.dataDirs || [],
  })
  const wechatRoots = deriveWeChatRoots(config, {
    explicitRoots: opts.videoRoot || [],
    checkDataDirs: check?.wechatClient?.dataDirs || [],
  })

  const outputDir = opts.dryRun ? null : await resolveOutputDir(opts, protectedRoots, warnings)
  if (opts.dryRun) {
    const plannedOutput = opts.output ? resolve(expandHome(opts.output)) : defaultOutputDir()
    for (const root of protectedRoots) {
      if (isPathInside(root, plannedOutput)) {
        throw new UsageError('输出目录不能放在微信数据目录内（源数据只读保护）', {
          code: 'OUTPUT_INSIDE_WECHAT',
        })
      }
    }
    const plan = {
      success: true,
      dryRun: true,
      outputDir: plannedOutput,
      time: {
        label: time.label,
        date: time.dateArg || null,
        from: time.fromArg || null,
        to: time.toArg || null,
      },
      types: [...types],
      mediaStrategy: opts.mediaStrategy,
      mediaEnabled,
      videoEnabled: videoRequested && opts.videoScan !== false,
      targets: targets.map((target) => ({
        id: target.id,
        name: target.name,
        kind: target.kind,
        lastTimestamp: target.lastTimestamp || null,
      })),
      warnings,
    }
    if (opts.json) {
      jsonResult(plan)
    } else {
      log('导出计划（dry-run，不写文件）')
      log(`  输出: ${plannedOutput}`)
      log(`  时间: ${time.label}`)
      log(`  类型: ${[...types].join(', ')}`)
      log(`  媒体: ${mediaEnabled ? opts.mediaStrategy : '不抽取'}`)
      log(`  视频: ${videoRequested && opts.videoScan !== false ? '扫描 msg/video' : '跳过'}`)
      log(`  会话: ${targets.length} 个`)
      for (const target of targets) log(`    - ${targetLabel(target)}`)
      if (warnings.length) {
        log('')
        for (const warning of warnings) log(`  提醒: ${warning}`)
      }
    }
    return
  }

  const video = await readVideoIndex({
    ctx,
    opts,
    check,
    warnings,
    videoRequested,
    wechatRoots,
  })

  const bundleSummaries = []
  const failures = []
  const usedDirs = new Set()

  for (let index = 0; index < targets.length; index += 1) {
    const target = targets[index]
    const dirName = targetDirName(target, usedDirs)
    const bundleDir = join(outputDir, dirName)
    const rawDir = join(bundleDir, 'raw')
    await ensureDir(rawDir)
    progress(opts, `[${index + 1}/${targets.length}] ${targetLabel(target)}`)

    try {
      const jsonResult = exportJson(ctx, {
        talker: target.id,
        outputDir: rawDir,
        dateArg: time.dateArg,
        fromArg: time.fromArg,
        toArg: time.toArg,
        limit: opts.limit,
        timeoutMs: opts.timeoutMinutes * 60_000,
      })

      let rawMessages = []
      let coverage = null
      if (jsonResult?.success === true) {
        const reported = jsonResult.path || join(rawDir, `${target.id}_messages.json`)
        const jsonPath = isAbsolute(reported) ? reported : resolve(rawDir, reported)
        if (!(await pathExists(jsonPath))) {
          throw new ExportError(`导出 JSON 不存在: ${jsonPath}`, { code: 'EXPORT_JSON_MISSING' })
        }
        const parsed = await readRawMessages(jsonPath)
        rawMessages = parsed.messages
        coverage = parsed.coverage
      } else if (String(jsonResult?.error || '').includes('未找到消息')) {
        warnings.push(`${target.name || target.id}: 该时间范围内没有消息`)
      } else {
        throw new ExportError(jsonResult?.error || 'weflow-cli JSON 导出失败', {
          code: jsonResult?.code || 'JSON_EXPORT_FAILED',
        })
      }

      if (coverage?.mayHaveMore === true) {
        warnings.push(`${target.name || target.id}: 可能还有更多消息未返回（coverage.mayHaveMore=true）`)
      }
      rawMessages = rawMessages.filter((message) => isWithinWindow(message.createTime, time))

      const mediaState = {
        requested: mediaEnabled,
        performed: false,
        skipped: false,
        failed: false,
        strategy: opts.mediaStrategy,
        strategyUsed: null,
        runs: 0,
        completed: 0,
        error: null,
      }

      if (mediaEnabled && pyReady) {
        const media = await extractMediaForTarget({
          ctx,
          target,
          rawDir,
          rawMessages,
          time,
          opts,
          warnings,
        })
        mediaState.performed = media.performed
        mediaState.failed = media.failed
        mediaState.strategyUsed = media.strategyUsed
        mediaState.runs = media.runs
        mediaState.completed = media.completed
        mediaState.error = media.error
        if (media.failed) warnings.push(`${target.name || target.id}: 媒体抽取未完成，缺失项已标注`)
      } else {
        mediaState.skipped = true
      }

      const mediaManifest = mediaState.performed
        ? await readMediaManifest(rawDir, target.id)
        : { records: {}, documents: {}, path: null }

      const bundleWarnings = []
      const bundle = await buildTargetBundle({
        target,
        rawMessages,
        rawDir,
        mediaManifest,
        mediaMap,
        revokeMap,
        revokeReport,
        videoIndex: video.index,
        contactMap,
        bundleDir,
        types,
        mediaState,
        warnings: bundleWarnings,
        meta: {
          weflowVersion,
          mediaStrategy: mediaState.strategyUsed || opts.mediaStrategy,
        },
        queryLabel: time.label,
        queryDate: time.dateArg,
        queryFrom: time.fromArg,
        queryTo: time.toArg,
        limit: opts.limit,
        meLabel: opts.meLabel || '我',
        ownWxid: config.wxid || '',
        includeRaw: Boolean(opts.includeRaw),
        html: opts.html !== false,
        markdown: opts.markdown !== false,
        maxHtmlMessages: opts.maxHtmlMessages,
        videoCandidates: parseVideoMd5Candidates,
        videoRootsFound: Boolean(video.roots.length || video.index?.files?.length),
      })
      for (const warning of bundleWarnings) warnings.push(`${target.name || target.id}: ${warning}`)

      if (opts.pruneRaw) await rmQuiet(rawDir)

      bundleSummaries.push({
        target: {
          id: target.id,
          name: target.name || target.id,
          kind: target.kind,
        },
        dir: dirName,
        stats: bundle.manifest.stats,
        html: Boolean(bundle.manifest.files.html),
        markdown: Boolean(bundle.manifest.files.markdown),
        warnings: bundle.manifest.warnings,
      })
      progress(
        opts,
        `    完成：${bundle.manifest.stats.exportedMessages} 条，`
        + `图片 ${bundle.manifest.stats.media.images.found}，`
        + `文件 ${bundle.manifest.stats.media.files.found}，`
        + `视频 ${bundle.manifest.stats.media.videos.found}`,
      )
    } catch (error) {
      const message = error?.message || String(error)
      if (!opts.allowPartial) throw error
      failures.push({
        target: { id: target.id, name: target.name || target.id, kind: target.kind },
        code: error?.code || 'TARGET_FAILED',
        error: message,
      })
      warnings.push(`${target.name || target.id}: 导出失败（${message}）`)
      progress(opts, `    失败：${message}`)
    }
  }

  const manifest = await writeBatchOutputs({
    outputDir,
    time,
    types,
    bundles: bundleSummaries,
    failures,
    warnings,
    weflowVersion,
  })

  if (opts.json) {
    jsonResult({
      success: failures.length === 0,
      partial: failures.length > 0,
      outputDir,
      manifest: join(outputDir, 'manifest.json'),
      indexPath: join(outputDir, 'index.html'),
      totals: manifest.totals,
      bundles: manifest.bundles,
      failures: manifest.failures,
      warnings: manifest.warnings,
    })
  } else {
    printHumanSummary({ outputDir, manifest, warnings })
  }

  if (failures.length) {
    const error = new PartialError(`有 ${failures.length} 个会话未完成`, { code: 'PARTIAL' })
    process.exitCode = error.exitCode
  }
}

async function main() {
  let opts = {}
  try {
    const parsed = parseArgv(process.argv.slice(2))
    opts = parsed.opts
    if (parsed.positionals.length) {
      throw new UsageError(`多余的位置参数: ${parsed.positionals.join(' ')}`, { code: 'UNEXPECTED_POSITIONAL' })
    }
    if (opts.help) {
      printUsage()
      return
    }
    if (opts.version) {
      console.log(`wechat-export ${SKILL_VERSION}`)
      return
    }

    opts.limit = numberOption(opts, 'limit', 0, 0, 1_000_000)
    opts.maxTargets = opts.maxTargets === undefined
      ? undefined
      : numberOption(opts, 'maxTargets', undefined, 1, 500)
    opts.maxHtmlMessages = numberOption(opts, 'maxHtmlMessages', 50_000, 0, 1_000_000)
    opts.timeoutMinutes = numberOption(opts, 'timeoutMinutes', 30, 1, 240)
    opts.mediaStrategy = String(opts.mediaStrategy || 'auto').toLowerCase()
    if (!['auto', 'day', 'full', 'none'].includes(opts.mediaStrategy)) {
      throw new UsageError('--media-strategy 只支持 auto | day | full | none', { code: 'INVALID_MEDIA_STRATEGY' })
    }
    if (opts.keepRaw && opts.pruneRaw) {
      throw new UsageError('--keep-raw 与 --prune-raw 不能同时使用', { code: 'CONFLICTING_OPTIONS' })
    }
    if (opts.media === false && opts.mediaStrategy === 'auto') opts.mediaStrategy = 'none'

    if (opts.check) {
      await runCheck(opts)
      return
    }
    if (opts.list || opts.listContacts) {
      await runList(opts)
      return
    }
    await runExport(opts)
  } catch (error) {
    handleError(error, opts)
  }
}

main()
