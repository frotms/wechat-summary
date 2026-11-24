import { promises as fs } from 'node:fs'
import { join } from 'node:path'

export const STATE_SCHEMA = 'wechat-export-state/v1'
export const REPORT_SCHEMA = 'wechat-export-incremental/v1'

async function readJson(path) {
  try {
    return JSON.parse(await fs.readFile(path, 'utf8'))
  } catch {
    return null
  }
}

async function readJsonl(path) {
  try {
    const text = await fs.readFile(path, 'utf8')
    const rows = []
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue
      rows.push(JSON.parse(line))
    }
    return rows
  } catch {
    return []
  }
}

export async function loadRecords(bundleDir) {
  const jsonl = await readJsonl(join(bundleDir, 'messages.jsonl'))
  if (jsonl.length) return jsonl
  const json = await readJson(join(bundleDir, 'messages.json'))
  return Array.isArray(json?.messages) ? json.messages : []
}

export async function loadMediaEntries(bundleDir) {
  const index = await readJson(join(bundleDir, 'media', 'index.json'))
  return Array.isArray(index?.items) ? index.items : []
}

export async function loadState(bundleDir) {
  const state = await readJson(join(bundleDir, 'state.json'))
  return state && typeof state === 'object' ? state : null
}

export async function loadReport(bundleDir) {
  const report = await readJson(join(bundleDir, 'incremental.json'))
  return report && typeof report === 'object' ? report : null
}

export function stateFromRecords(records, target = {}) {
  const ordered = sortRecords(records)
  const last = ordered.length ? ordered[ordered.length - 1] : null
  return {
    schema: STATE_SCHEMA,
    target: {
      id: target.id || '',
      name: target.name || target.id || '',
      kind: target.kind || '',
    },
    lastTimestamp: Number(last?.timestamp) || 0,
    lastMessageId: last?.id || '',
    messageCount: ordered.length,
    updatedAt: new Date().toISOString(),
    derived: true,
  }
}

export function sortRecords(records) {
  return [...(records || [])].sort((a, b) => {
    const at = Number(a?.timestamp) || 0
    const bt = Number(b?.timestamp) || 0
    if (at !== bt) return at - bt
    return String(a?.localId ?? '').localeCompare(String(b?.localId ?? ''), 'en', { numeric: true })
  })
}

export async function writeState(bundleDir, state) {
  const value = {
    schema: STATE_SCHEMA,
    ...state,
    updatedAt: new Date().toISOString(),
  }
  await fs.writeFile(join(bundleDir, 'state.json'), `${JSON.stringify(value, null, 2)}\n`, 'utf8')
  return value
}

export async function writeReport(bundleDir, report) {
  const value = {
    schema: REPORT_SCHEMA,
    generatedAt: new Date().toISOString(),
    ...report,
  }
  await fs.writeFile(join(bundleDir, 'incremental.json'), `${JSON.stringify(value, null, 2)}\n`, 'utf8')
  return value
}

function duplicateIds(records) {
  const seen = new Set()
  const duplicates = []
  for (const record of records) {
    const id = record?.id
    if (!id) continue
    if (seen.has(id)) duplicates.push(id)
    seen.add(id)
  }
  return duplicates
}

function isSorted(records) {
  for (let index = 1; index < records.length; index += 1) {
    const prev = Number(records[index - 1]?.timestamp) || 0
    const next = Number(records[index]?.timestamp) || 0
    if (next < prev) return false
  }
  return true
}

async function missingMedia(bundleDir, entries) {
  const missing = []
  for (const entry of entries || []) {
    if (!entry?.path) continue
    try {
      await fs.access(join(bundleDir, entry.path))
    } catch {
      missing.push(entry.path)
    }
  }
  return missing
}

function duplicateMedia(entries) {
  const byHash = new Map()
  const duplicates = []
  for (const entry of entries || []) {
    const hash = entry?.sha256
    if (!hash || !entry.path) continue
    const previous = byHash.get(hash)
    if (previous && previous !== entry.path) {
      duplicates.push({ sha256: hash, paths: [previous, entry.path] })
    } else {
      byHash.set(hash, entry.path)
    }
  }
  return duplicates
}

/**
 * Validate a bundle's incremental state/report and the resulting JSONL.
 *
 * This check is deliberately bundle-local: it does not need the WeChat DB.
 * It catches the common failure modes (state behind the data, duplicate IDs,
 * lost/duplicated fetched messages, unsorted output, missing media, media
 * copied twice under different names).
 */
export async function checkIncrementalBundle(bundleDir) {
  const [state, report, records, mediaEntries] = await Promise.all([
    loadState(bundleDir),
    loadReport(bundleDir),
    loadRecords(bundleDir),
    loadMediaEntries(bundleDir),
  ])

  if (!state && !report) {
    return {
      ok: true,
      incremental: false,
      skipped: true,
      reason: 'no-incremental-state',
      bundleDir,
      records: records.length,
    }
  }

  const sorted = sortRecords(records)
  const ids = duplicateIds(records)
  const maxTimestamp = records.reduce((value, record) => Math.max(value, Number(record?.timestamp) || 0), 0)
  const lastRecord = sorted.length ? sorted[sorted.length - 1] : null
  const missing = await missingMedia(bundleDir, mediaEntries)
  const duplicateMediaEntries = duplicateMedia(mediaEntries)
  const check = report?.check && typeof report.check === 'object' ? report.check : {}
  const previousMessages = Number(report?.previousMessages) || 0
  const newMessages = Number(report?.newMessages) || 0
  const parsedRecords = Number(report?.parsedRecords) || 0
  const duplicateMessages = Number(report?.duplicateMessages) || 0
  const totalMessages = Number(report?.totalMessages) || records.length
  const stateMatches = !state || (
    Number(state.messageCount) === records.length
    && Number(state.lastTimestamp) === maxTimestamp
    && (!state.lastMessageId || state.lastMessageId === lastRecord?.id)
  )
  const countsMatch = !report || (
    previousMessages + newMessages === totalMessages
    && newMessages + duplicateMessages === parsedRecords
    && totalMessages === records.length
  )
  const checks = {
    stateExists: Boolean(state),
    reportExists: Boolean(report),
    stateMatches,
    countsMatch,
    sorted: sorted.every((record, index) => record.id === records[index]?.id),
    duplicateIds: ids,
    duplicateMessages,
    missingMedia: missing,
    duplicateMedia: duplicateMediaEntries,
    reportOk: report ? check.ok !== false : true,
  }
  const ok = Boolean(
    state
    && report
    && stateMatches
    && countsMatch
    && checks.sorted
    && ids.length === 0
    && missing.length === 0
    && checks.reportOk
  )

  return {
    ok,
    incremental: true,
    bundleDir,
    target: report?.target || state?.target || null,
    records: records.length,
    maxTimestamp,
    lastMessageId: lastRecord?.id || '',
    checks,
    problems: [
      ...(state ? [] : ['缺少 state.json']),
      ...(report ? [] : ['缺少 incremental.json']),
      ...(stateMatches ? [] : ['state 与 messages.jsonl 不一致']),
      ...(countsMatch ? [] : ['增量计数不闭合']),
      ...(checks.sorted ? [] : ['messages.jsonl 未按时间排序']),
      ...(ids.length ? [`重复消息 id ${ids.length} 个`] : []),
      ...(missing.length ? [`缺失媒体 ${missing.length} 个`] : []),
      ...(checks.reportOk ? [] : ['导出时增量自检未通过']),
    ],
    // Old bundles may already contain byte-identical media under different
    // names. That is reported for cleanup but is not an incremental failure:
    // new media is deduplicated by sha256.
    warnings: duplicateMediaEntries.length
      ? [`媒体重复 ${duplicateMediaEntries.length} 组（旧导出遗留）`]
      : [],
  }
}

export async function checkIncrementalOutput(outputDir) {
  const manifest = await readJson(join(outputDir, 'manifest.json'))
  const bundles = Array.isArray(manifest?.bundles) ? manifest.bundles : []
  const results = []
  for (const entry of bundles) {
    const dir = join(outputDir, String(entry?.dir || ''))
    const result = await checkIncrementalBundle(dir)
    results.push({
      target: entry?.target || result.target,
      dir: entry?.dir || '',
      ...result,
    })
  }
  const checked = results.filter((result) => result.incremental)
  return {
    ok: checked.length > 0 && checked.every((result) => result.ok),
    output: outputDir,
    bundles: results.length,
    incrementalBundles: checked.length,
    failed: checked.filter((result) => !result.ok).length,
    results,
  }
}
