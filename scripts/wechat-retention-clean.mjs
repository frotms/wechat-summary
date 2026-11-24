#!/usr/bin/env node
import { resolve } from 'node:path'
import { removeExpiredExports } from './lib/retention.mjs'

const argv = process.argv.slice(2)
let output = ''
let yes = false
let json = false
for (let i = 0; i < argv.length; i += 1) {
  const token = argv[i]
  if (token === '--output' || token === '-o') { output = argv[++i] || ''; continue }
  if (token.startsWith('--output=')) { output = token.slice('--output='.length); continue }
  if (token === '--yes') { yes = true; continue }
  if (token === '--json') { json = true; continue }
  if (token === '-h' || token === '--help') {
    console.log('用法: node scripts/wechat-retention-clean.mjs --output <导出根目录> [--yes] [--json]')
    process.exit(0)
  }
  console.error(`未知参数: ${token}`)
  process.exit(2)
}
if (!output) {
  console.error('需要 --output <导出根目录>')
  process.exit(2)
}

const result = await removeExpiredExports(resolve(output), { yes })
if (json) {
  console.log(JSON.stringify({ success: true, dryRun: !yes, ...result }, null, 2))
} else {
  if (!result.expired.length) {
    console.log('没有到期的导出目录')
  } else {
    console.log(`${yes ? '已删除' : '将删除'} ${result.expired.length} 个到期目录：`)
    for (const item of result.expired) {
      console.log(`  ${item.name}  deleteAfter=${item.deleteAfter}`)
    }
    if (!yes) console.log('加 --yes 实际删除')
  }
}
