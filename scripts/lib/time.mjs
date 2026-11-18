import { UsageError } from './errors.mjs'

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

function pad(n) {
  return String(n).padStart(2, '0')
}

export function parseDateOnly(text, endOfDay = false) {
  const match = String(text || '').match(DATE_ONLY)
  if (!match) return null
  const [, y, m, d] = match
  const date = new Date(
    Number(y), Number(m) - 1, Number(d),
    endOfDay ? 23 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 999 : 0,
  )
  const valid = date.getFullYear() === Number(y)
    && date.getMonth() === Number(m) - 1
    && date.getDate() === Number(d)
  return valid ? date : null
}

export function parseTimeInput(text, endOfDay = false) {
  if (!text) return null
  const dateOnly = parseDateOnly(text, endOfDay)
  if (dateOnly) return dateOnly
  const ms = Date.parse(String(text))
  if (Number.isNaN(ms)) {
    throw new UsageError(`时间格式无效: ${text}（要用 YYYY-MM-DD 或 ISO 时间）`, { code: 'INVALID_DATE' })
  }
  return new Date(ms)
}

export function toEpochSeconds(date) {
  return Math.floor(date.getTime() / 1000)
}

export function toLocalDateString(input) {
  const date = input instanceof Date ? input : new Date(Number(input) * 1000)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function toLocalDateTime(input) {
  const date = input instanceof Date ? input : new Date(Number(input) * 1000)
  return `${toLocalDateString(date)} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function toIso(input) {
  const date = input instanceof Date ? input : new Date(Number(input) * 1000)
  return date.toISOString()
}

export function localDateOf(timestampSeconds) {
  return toLocalDateString(new Date(Number(timestampSeconds) * 1000))
}

export function nowStamp(now = new Date()) {
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-`
    + `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
    + `-${String(now.getMilliseconds()).padStart(3, '0')}`
}

/**
 * Resolve the user-facing time options into:
 *   - fromTs / toTs: epoch seconds, inclusive
 *   - fromArg / toArg: values to hand to `weflow-cli export --from/--to`
 *   - dateArg: value for `--date` (mutually exclusive with from/to)
 *
 * `--to YYYY-MM-DD` is inclusive: the whole day is exported, matching
 * weflow-cli's own date semantics.
 */
export function resolveTimeWindow(opts, now = new Date()) {
  const hasFromOrTo = Boolean(opts.from || opts.to)
  if (opts.date && (hasFromOrTo || opts.days)) {
    throw new UsageError('--date 不能与 --from/--to/--days 同时使用', { code: 'CONFLICTING_DATE_FILTERS' })
  }
  if (opts.days && hasFromOrTo) {
    throw new UsageError('--days 不能与 --from/--to 同时使用', { code: 'CONFLICTING_DATE_FILTERS' })
  }

  if (opts.date) {
    const start = parseDateOnly(opts.date, false)
    const end = parseDateOnly(opts.date, true)
    if (!start || !end) {
      throw new UsageError(`--date 无效: ${opts.date}（要用 YYYY-MM-DD）`, { code: 'INVALID_DATE' })
    }
    return {
      kind: 'date',
      dateArg: opts.date,
      fromArg: undefined,
      toArg: undefined,
      fromTs: toEpochSeconds(start),
      toTs: toEpochSeconds(end),
      fromDate: opts.date,
      toDate: opts.date,
      label: opts.date,
    }
  }

  if (opts.days !== undefined) {
    const days = Number(opts.days)
    if (!Number.isInteger(days) || days < 1 || days > 3650) {
      throw new UsageError('--days 必须是 1..3650 的整数', { code: 'INVALID_DAYS' })
    }
    const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999)
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (days - 1), 0, 0, 0, 0)
    const fromDate = toLocalDateString(start)
    const toDate = toLocalDateString(end)
    return {
      kind: 'range',
      dateArg: undefined,
      fromArg: fromDate,
      toArg: toDate,
      fromTs: toEpochSeconds(start),
      toTs: toEpochSeconds(end),
      fromDate,
      toDate,
      label: `${fromDate} ~ ${toDate}（最近 ${days} 天）`,
    }
  }

  if (hasFromOrTo) {
    const from = opts.from ? parseTimeInput(opts.from, false) : null
    const to = opts.to ? parseTimeInput(opts.to, true) : null
    const fromTs = from ? toEpochSeconds(from) : undefined
    const toTs = to ? toEpochSeconds(to) : undefined
    if (fromTs !== undefined && toTs !== undefined && fromTs > toTs) {
      throw new UsageError('--from 不能晚于 --to', { code: 'INVALID_DATE_RANGE' })
    }
    return {
      kind: 'range',
      dateArg: undefined,
      fromArg: opts.from,
      toArg: opts.to,
      fromTs,
      toTs,
      fromDate: from ? toLocalDateString(from) : undefined,
      toDate: to ? toLocalDateString(to) : undefined,
      label: `${opts.from || '最早'} ~ ${opts.to || '现在'}`,
    }
  }

  return {
    kind: 'all',
    dateArg: undefined,
    fromArg: undefined,
    toArg: undefined,
    fromTs: undefined,
    toTs: undefined,
    fromDate: undefined,
    toDate: undefined,
    label: '全部时间',
  }
}

/** Inclusive calendar days between two YYYY-MM-DD strings. */
export function eachDate(fromDate, toDate, maxDays = 3660) {
  const start = parseDateOnly(fromDate, false)
  const end = parseDateOnly(toDate, false)
  if (!start || !end || start > end) return []
  const out = []
  const cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate())
  const last = new Date(end.getFullYear(), end.getMonth(), end.getDate())
  while (cursor <= last && out.length < maxDays) {
    out.push(toLocalDateString(cursor))
    cursor.setDate(cursor.getDate() + 1)
  }
  return out
}

export function dayCount(fromDate, toDate) {
  return eachDate(fromDate, toDate).length
}

export function isWithinWindow(timestampSeconds, window) {
  const ts = Number(timestampSeconds)
  if (!Number.isFinite(ts)) return false
  if (window.fromTs !== undefined && ts < window.fromTs) return false
  if (window.toTs !== undefined && ts > window.toTs) return false
  return true
}
