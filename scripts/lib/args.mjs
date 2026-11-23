import { UsageError } from './errors.mjs'

const BOOLEAN = new Set([
  'help', 'version', 'json', 'verbose', 'check',
  'list', 'listContacts', 'includeOfficial', 'includeSummary',
  'allGroups', 'allPersons', 'allOfficial', 'includeEmpty',
  'allTime', 'media', 'fullImages', 'videoScan',
  'force', 'html', 'markdown', 'includeRaw',
  'keepRaw', 'pruneRaw', 'dryRun', 'allowPartial',
  'fetchArticles', 'downloadStickers',
])

const MULTI = new Set(['target', 'group', 'person', 'videoRoot'])

const VALUES = new Set([
  'keyword', 'maxTargets', 'from', 'to', 'date', 'days', 'limit',
  'types', 'mediaStrategy', 'weflowConfig', 'output', 'mediaMap', 'revokeMap',
  'maxHtmlMessages', 'weflow', 'timeoutMinutes', 'meLabel',
])

const ALIASES = {
  '-h': 'help',
  '-v': 'version',
  '-o': 'output',
  '-n': 'limit',
  '-k': 'keyword',
  '-d': 'date',
  '-t': 'types',
  '--all': 'allTime',
}

function camel(name) {
  return name.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
}

function coerceBoolean(raw, name) {
  if (raw === undefined) return true
  const text = String(raw).toLowerCase()
  if (['1', 'true', 'yes', 'on'].includes(text)) return true
  if (['0', 'false', 'no', 'off'].includes(text)) return false
  throw new UsageError(`--${name} 的值必须是 true/false`, { code: 'INVALID_ARGUMENT' })
}

/**
 * Parse a small, explicit argv surface. Unknown flags are rejected instead of
 * being ignored: a typo in `--from` silently exporting all history is worse
 * than a clear error.
 */
export function parseArgv(argv) {
  const opts = {}
  const positionals = []

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i]
    if (token === '--') {
      positionals.push(...argv.slice(i + 1))
      break
    }
    if (!token.startsWith('-') || token === '-') {
      positionals.push(token)
      continue
    }

    let name
    let value
    if (token.startsWith('--')) {
      const eq = token.indexOf('=')
      if (eq >= 0) {
        name = token.slice(2, eq)
        value = token.slice(eq + 1)
      } else {
        name = token.slice(2)
      }
      if (name.startsWith('no-') && !name.startsWith('non-')) {
        const positive = camel(name.slice(3))
        if (!BOOLEAN.has(positive)) {
          throw new UsageError(`未知参数: --${name}`, { code: 'UNKNOWN_OPTION' })
        }
        opts[positive] = false
        continue
      }
    } else {
      name = ALIASES[token] || token.slice(1)
    }

    name = camel(name)
    if (!BOOLEAN.has(name) && !MULTI.has(name) && !VALUES.has(name)) {
      throw new UsageError(`未知参数: ${token}`, { code: 'UNKNOWN_OPTION' })
    }

    if (BOOLEAN.has(name)) {
      opts[name] = coerceBoolean(value, name)
      continue
    }

    if (value === undefined) {
      const next = argv[i + 1]
      if (next === undefined || (next.startsWith('-') && next !== '-')) {
        throw new UsageError(`--${name} 需要一个值`, { code: 'MISSING_VALUE' })
      }
      value = next
      i += 1
    }

    if (MULTI.has(name)) {
      ;(opts[name] ||= []).push(value)
    } else {
      opts[name] = value
    }
  }

  return { opts, positionals }
}

export const OPTION_SETS = { BOOLEAN, MULTI, VALUES }
