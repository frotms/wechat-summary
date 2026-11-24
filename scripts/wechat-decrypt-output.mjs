#!/usr/bin/env node
import { resolve } from 'node:path'
import { decryptBundle } from './lib/crypto-bundle.mjs'

function usage() {
  console.log(`wechat-decrypt-output

解密 wechat-export 生成的 .wxenc 加密包。

用法:
  WECHAT_EXPORT_PASSWORD=<密码> node scripts/wechat-decrypt-output.mjs \\
    --input <包文件> --output <目录> [--force] [--json]
`)
}

const argv = process.argv.slice(2)
let input = ''
let output = ''
let force = false
let json = false
for (let i = 0; i < argv.length; i += 1) {
  const token = argv[i]
  if (token === '-h' || token === '--help') { usage(); process.exit(0) }
  if (token === '--input' || token === '-i') { input = argv[++i] || ''; continue }
  if (token === '--output' || token === '-o') { output = argv[++i] || ''; continue }
  if (token.startsWith('--input=')) { input = token.slice('--input='.length); continue }
  if (token.startsWith('--output=')) { output = token.slice('--output='.length); continue }
  if (token === '--force') { force = true; continue }
  if (token === '--json') { json = true; continue }
  console.error(`未知参数: ${token}`)
  process.exit(2)
}
if (!input || !output) {
  console.error('需要 --input <包文件> 和 --output <目录>')
  process.exit(2)
}
const password = process.env.WECHAT_EXPORT_PASSWORD || ''
if (!password) {
  console.error('需要设置环境变量 WECHAT_EXPORT_PASSWORD')
  process.exit(2)
}

try {
  const result = await decryptBundle(resolve(input), resolve(output), password, { force })
  if (json) {
    console.log(JSON.stringify({ success: true, ...result }, null, 2))
  } else {
    console.log(`解密完成：${result.files} 个文件 -> ${result.output}`)
  }
} catch (error) {
  if (json) {
    console.log(JSON.stringify({ success: false, error: String(error?.message || error) }, null, 2))
  } else {
    console.error(`解密失败：${error?.message || error}`)
  }
  process.exit(5)
}
