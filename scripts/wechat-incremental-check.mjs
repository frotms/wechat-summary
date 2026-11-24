#!/usr/bin/env node
import { resolve } from 'node:path'
import { checkIncrementalOutput } from './lib/incremental.mjs'

function usage() {
  console.log(`wechat-incremental-check

校验按会话增量导出的状态、计数和媒体去重是否正确。

用法:
  node scripts/wechat-incremental-check.mjs --output <导出目录> [--json]

检查项:
  state.json 与 messages.jsonl 一致
  incremental.json 计数闭合（旧消息 + 新增 = 总数）
  无重复消息 id、按时间排序
  媒体文件存在，且同一 sha256 不重复落盘
`)
}

const argv = process.argv.slice(2)
let output = ''
let json = false
for (let i = 0; i < argv.length; i += 1) {
  const token = argv[i]
  if (token === '-h' || token === '--help') { usage(); process.exit(0) }
  if (token === '--json') { json = true; continue }
  if (token === '--output' || token === '-o') { output = argv[++i] || ''; continue }
  if (token.startsWith('--output=')) { output = token.slice('--output='.length); continue }
  console.error(`未知参数: ${token}`)
  process.exit(2)
}
if (!output) {
  console.error('需要 --output <导出目录>')
  process.exit(2)
}

const report = await checkIncrementalOutput(resolve(output))
if (json) {
  console.log(JSON.stringify(report, null, 2))
} else {
  console.log(`增量校验：${report.incrementalBundles}/${report.bundles} 个会话启用增量，`
    + `${report.failed} 个失败`)
  for (const result of report.results) {
    const name = result.target?.name || result.dir || '?'
    if (!result.incremental) {
      console.log(`  - ${name}: 未启用增量（无 state.json）`)
      continue
    }
    if (result.ok) {
      console.log(`  ✓ ${name}: ${result.records} 条，最后 ${result.maxTimestamp}`)
    } else {
      console.log(`  ✗ ${name}: ${result.problems.join('；') || '校验失败'}`)
    }
    for (const warning of result.warnings || []) {
      console.log(`    ! ${warning}`)
    }
  }
}
process.exitCode = report.ok ? 0 : 5
