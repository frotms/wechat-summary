#!/usr/bin/env node
/**
 * WeChat 4.1 (weflow-cli) export entry with automatic voice mapping.
 *
 * 4.1's weflow-cli export only labels voice messages. This wrapper:
 *   1. resolves targets/time with wechat-export.mjs --dry-run
 *   2. runs wechat41-voice-map.py for each target (SILK -> WAV)
 *   3. runs the normal wechat-export.mjs with --media-map
 *
 * It uses the real ~/.weflow-cli/config.json (or --config), no sandbox.
 */
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { nowStamp } from './lib/time.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const MAIN = join(HERE, 'wechat-export.mjs')
const VOICE_MAP = join(HERE, 'wechat41-voice-map.py')
const STICKER_MAP = join(HERE, 'wechat-sticker-map.py')
const STICKER_SEED = join(HERE, 'wechat-sticker-seed.py')
const REVOKE_MAP = join(HERE, 'wechat-revoke-map.py')
const AUDIT = join(HERE, 'wechat-audit.py')

function usage() {
  console.log(`WeChat 4.1 export (automatic voice map)

用法:
  node scripts/wechat41-export.mjs [正常的 wechat-export 参数]
  node scripts/wechat41-export.mjs audit --output <导出目录> [--json]

额外选项:
  --weflow-cli-dir <scripts 目录>  默认从 npm root -g 查找 weflow-cli/scripts
  --config <config.json>           默认 ~/.weflow-cli/config.json
  --no-voice-map                   跳过语音映射（只走 weflow-cli）
  --no-sticker-map                 跳过表情包映射（表情只保留文字标签）
  --sticker-seed <值>              表情包解密种子（缺省自动探测）
  --no-revoke-map                  跳过撤回记录映射
  --no-audit                       导出后不跑完整性校验

示例:
  node scripts/wechat41-export.mjs --target "项目群" --days 7
  node scripts/wechat41-export.mjs --target "wxid_xxx" --date 2026-11-13 --types all
`)
}

function fail(message, code = 2) {
  console.error(`✗ ${message}`)
  process.exit(code)
}

function findVenvPython() {
  if (process.env.WEFLOW_PYTHON) return process.env.WEFLOW_PYTHON
  const candidates = process.platform === 'win32'
    ? [join(homedir(), '.weflow-cli', 'venv', 'Scripts', 'python.exe')]
    : [join(homedir(), '.weflow-cli', 'venv', 'bin', 'python')]
  return candidates.find((path) => existsSync(path)) || (process.platform === 'win32' ? 'python.exe' : 'python3')
}

function npmGlobalRoot() {
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  const res = spawnSync(npm, ['root', '-g'], {
    encoding: 'utf8', windowsHide: true, timeout: 20_000, shell: process.platform === 'win32',
  })
  if (res.status === 0) {
    const lines = String(res.stdout || '').trim().split(/\r?\n/).filter(Boolean)
    if (lines.length) return lines[lines.length - 1]
  }
  return ''
}

function findScriptsDir(explicit) {
  const candidates = []
  if (explicit) candidates.push(explicit)
  if (process.env.WEFLOW_CLI_SCRIPTS) candidates.push(process.env.WEFLOW_CLI_SCRIPTS)
  const root = npmGlobalRoot()
  if (root) candidates.push(join(root, 'weflow-cli', 'scripts'))
  candidates.push(join(process.env.APPDATA || '', 'npm', 'node_modules', 'weflow-cli', 'scripts'))
  candidates.push('/usr/local/lib/node_modules/weflow-cli/scripts')
  for (const candidate of candidates) {
    if (candidate && existsSync(join(candidate, 'wechat_voice.py'))) return resolve(candidate)
  }
  fail('找不到 weflow-cli/scripts；用 --weflow-cli-dir 指定')
}

function parseFlags(args) {
  const values = {}
  const rest = []
  for (let i = 0; i < args.length; i += 1) {
    const token = args[i]
    if (token === '--no-voice-map') { values.noVoiceMap = true; continue }
    if (token === '--no-sticker-map') { values.noStickerMap = true; continue }
    if (token === '--no-revoke-map') { values.noRevokeMap = true; continue }
    if (token === '--no-audit') { values.noAudit = true; continue }
    if (token === '--weflow-cli-dir' || token === '--config' || token === '--sticker-seed') {
      values[token.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = args[++i]
      continue
    }
    rest.push(token)
  }
  return { values, rest }
}

function run(command, args, options = {}) {
  const res = spawnSync(command, args, {
    stdio: options.capture ? 'pipe' : 'inherit',
    encoding: 'utf8',
    windowsHide: true,
    env: options.env || process.env,
    timeout: options.timeoutMs || 0,
  })
  if (res.error) fail(`执行失败: ${command}: ${res.error.message}`, 4)
  if (options.capture) return res
  if (res.status !== 0 && !options.allowNonZero) process.exit(res.status ?? 4)
  return res
}

function dryRunTargets(configPath, args) {
  const res = run(process.execPath, [MAIN, ...args, '--dry-run', '--json', '--weflow-config', configPath], {
    capture: true, timeoutMs: 300_000,
  })
  if (res.status !== 0) return null
  try {
    return JSON.parse(res.stdout)
  } catch {
    return null
  }
}

function buildVoiceMap({ scriptsDir, configPath, python, targets, time, baseDir }) {
  const from = time?.date || time?.from || ''
  const to = time?.date || time?.to || ''
  const merged = { schema: 'wechat42-media-map-merged/v1', items: {}, sources: [] }
  mkdirSync(baseDir, { recursive: true })
  for (const target of targets) {
    const tmp = join(baseDir, `voice-${target.id.replace(/[^A-Za-z0-9_-]/g, '_')}-${nowStamp()}.json`)
    const res = run(python, [
      VOICE_MAP,
      '--weflow-cli-dir', scriptsDir,
      '--config', configPath,
      '--target', target.id,
      '--from', from,
      '--to', to,
      '--out', tmp,
    ], {
      capture: true,
      timeoutMs: 600_000,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    if (res.status !== 0) {
      console.error(`  ! 语音映射失败: ${target.name || target.id}`)
      const detail = String(res.stderr || res.stdout || '').trim()
      if (detail) console.error(`    ${detail.slice(-600)}`)
      continue
    }
    try {
      const data = JSON.parse(readFileSync(tmp, 'utf8'))
      for (const [key, value] of Object.entries(data.items || {})) merged.items[key] = value
      merged.sources.push({ target: target.id, counts: data.counts })
      rmSync(tmp, { force: true })
    } catch {
      console.error(`  ! 语音映射不可读: ${target.name || target.id}`)
    }
  }
  if (!Object.keys(merged.items).length) return null
  const out = join(baseDir, `voice-map-${nowStamp()}.json`)
  writeFileSync(out, JSON.stringify(merged, null, 2), 'utf8')
  return out
}

function buildStickerMap({ scriptsDir, configPath, python, targets, time, baseDir, seed }) {
  const from = time?.date || time?.from || ''
  const to = time?.date || time?.to || ''
  const merged = { schema: 'wechat42-media-map-merged/v1', items: {}, sources: [] }
  const stickerDir = join(baseDir, '_stickers')
  mkdirSync(stickerDir, { recursive: true })
  for (const target of targets) {
    const tmp = join(baseDir, `sticker-${target.id.replace(/[^A-Za-z0-9_-]/g, '_')}-${nowStamp()}.json`)
    const res = run(python, [
      STICKER_MAP,
      '--weflow-cli-dir', scriptsDir,
      '--config', configPath,
      '--target', target.id,
      '--from', from,
      '--to', to,
      '--out', tmp,
      '--out-dir', stickerDir,
      ...(seed ? ['--seed', seed] : []),
    ], {
      capture: true,
      timeoutMs: 900_000,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    if (res.status !== 0) {
      console.error(`  ! 表情映射失败: ${target.name || target.id}`)
      const detail = String(res.stderr || res.stdout || '').trim()
      if (detail) console.error(`    ${detail.slice(-600)}`)
      continue
    }
    try {
      const data = JSON.parse(readFileSync(tmp, 'utf8'))
      for (const [key, value] of Object.entries(data.items || {})) merged.items[key] = value
      merged.sources.push({ target: target.id, counts: data.counts })
      rmSync(tmp, { force: true })
    } catch {
      console.error(`  ! 表情映射不可读: ${target.name || target.id}`)
    }
  }
  if (!Object.keys(merged.items).length) return null
  const out = join(baseDir, `sticker-map-${nowStamp()}.json`)
  writeFileSync(out, JSON.stringify(merged, null, 2), 'utf8')
  return out
}

function resolveStickerSeed({ scriptsDir, configPath, python, baseDir, explicit }) {
  if (explicit) return String(explicit)
  const res = run(python, [
    STICKER_SEED,
    '--account-root', accountRootFromConfig(configPath),
    '--config', configPath,
  ], {
    capture: true,
    timeoutMs: 300_000,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
  })
  if (res.status !== 0) return ''
  try {
    const data = JSON.parse(res.stdout)
    if (data.seed) {
      console.error(`表情包种子: 已获取（来源 ${data.source}）`)
      return String(data.seed)
    }
  } catch {
    // fall through
  }
  console.error('  ! 没有拿到表情包种子；表情只保留文字标签')
  return ''
}

/** The account directory for a weflow config (sticker cache lives there). */
function accountRootFromConfig(configPath) {
  try {
    const cfg = JSON.parse(readFileSync(configPath, 'utf8'))
    const dbPath = cfg.ntDbPath || ''
    const wxid = cfg.wxid || ''
    if (!dbPath) return ''
    const parent = dirname(dbPath)
    if (wxid && existsSync(join(parent, wxid))) return join(parent, wxid)
    if (basename(dbPath) === wxid) return dbPath
    return dbPath
  } catch {
    return ''
  }
}

function buildRevokeMap({ scriptsDir, configPath, python, targets, time, baseDir }) {
  const from = time?.date || time?.from || ''
  const to = time?.date || time?.to || ''
  const merged = {
    schema: 'wechat-revoke-map-merged/v1', items: {}, orphans: [], sources: [],
  }
  for (const target of targets) {
    const tmp = join(baseDir, `revoke-${target.id.replace(/[^A-Za-z0-9_-]/g, '_')}-${nowStamp()}.json`)
    const res = run(python, [
      REVOKE_MAP,
      '--weflow-cli-dir', scriptsDir,
      '--config', configPath,
      '--target', target.id,
      '--from', from,
      '--to', to,
      '--out', tmp,
    ], {
      capture: true,
      timeoutMs: 300_000,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    if (res.status !== 0) {
      console.error(`  ! 撤回映射失败: ${target.name || target.id}`)
      const detail = String(res.stderr || res.stdout || '').trim()
      if (detail) console.error(`    ${detail.slice(-600)}`)
      continue
    }
    try {
      const data = JSON.parse(readFileSync(tmp, 'utf8'))
      for (const [key, value] of Object.entries(data.items || {})) {
        merged.items[key] = { ...value, target: target.id }
      }
      for (const orphan of data.orphans || []) {
        merged.orphans.push({ ...orphan, target: target.id })
      }
      merged.sources.push({ target: target.id, counts: data.counts })
      rmSync(tmp, { force: true })
    } catch {
      console.error(`  ! 撤回映射不可读: ${target.name || target.id}`)
    }
  }
  if (!Object.keys(merged.items).length && !merged.orphans.length) return null
  const out = join(baseDir, `revoke-map-${nowStamp()}.json`)
  writeFileSync(out, JSON.stringify(merged, null, 2), 'utf8')
  return out
}

function runAudit(args) {
  let output = ''
  let json = false
  for (let i = 0; i < args.length; i += 1) {
    const token = args[i]
    if (token === '--output' || token === '-o') { output = args[++i] || ''; continue }
    if (token.startsWith('--output=')) { output = token.slice('--output='.length); continue }
    if (token === '--json') { json = true; continue }
    fail(`未知的 audit 参数: ${token}`)
  }
  if (!output) fail('audit 需要 --output <导出目录>')
  const configPath = resolve(join(homedir(), '.weflow-cli', 'config.json'))
  if (!existsSync(configPath)) fail(`找不到 weflow-cli 配置: ${configPath}`, 3)
  const scriptsDir = findScriptsDir()
  const python = findVenvPython()
  const pythonArgs = [
    AUDIT,
    '--output', resolve(output),
    '--weflow-cli-dir', scriptsDir,
    '--config', configPath,
  ]
  if (json) pythonArgs.push('--json')
  const res = run(python, pythonArgs, {
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
  })
  process.exit(res.status ?? 0)
}

function main() {
  const argv = process.argv.slice(2)
  if (argv.includes('-h') || argv.includes('--help')) { usage(); return }
  if (argv[0] === 'audit') { runAudit(argv.slice(1)); return }
  const { values, rest } = parseFlags(argv)
  const configPath = resolve(values.config || join(homedir(), '.weflow-cli', 'config.json'))
  if (!existsSync(configPath)) fail(`找不到 weflow-cli 配置: ${configPath}`, 3)
  const scriptsDir = findScriptsDir(values.weflowCliDir)
  const python = findVenvPython()
  const baseDir = join(homedir(), '.wechat-export', 'wechat41')

  let mediaMap = null
  let revokeMap = null
  if (!values.noVoiceMap || !values.noStickerMap || !values.noRevokeMap) {
    const plan = dryRunTargets(configPath, rest)
    if (plan?.targets?.length) {
      if (!values.noVoiceMap) {
        const voiceMap = buildVoiceMap({
          scriptsDir, configPath, python, targets: plan.targets, time: plan.time, baseDir,
        })
        if (voiceMap) {
          mediaMap = voiceMap
          console.error(`语音映射: ${voiceMap}`)
        }
      }
      if (!values.noStickerMap) {
        const accountRoot = accountRootFromConfig(configPath)
        if (accountRoot) {
          const seed = resolveStickerSeed({ configPath, python, explicit: values.stickerSeed })
          if (seed) {
            const stickerMap = buildStickerMap({
              scriptsDir, configPath, python, targets: plan.targets, time: plan.time, baseDir, seed,
            })
            if (stickerMap) {
              if (mediaMap) {
                const current = JSON.parse(readFileSync(mediaMap, 'utf8'))
                const extra = JSON.parse(readFileSync(stickerMap, 'utf8'))
                Object.assign(current.items, extra.items)
                writeFileSync(mediaMap, JSON.stringify(current, null, 2), 'utf8')
                rmSync(stickerMap, { force: true })
              } else {
                mediaMap = stickerMap
              }
              console.error(`表情映射: ${stickerMap}`)
            }
          }
        }
      }
      if (!values.noRevokeMap) {
        const revoke = buildRevokeMap({
          scriptsDir, configPath, python, targets: plan.targets, time: plan.time, baseDir,
        })
        if (revoke) {
          revokeMap = revoke
          console.error(`撤回映射: ${revoke}`)
        }
      }
    } else {
      console.error('没有解析到目标，跳过语音/表情/撤回映射')
    }
  }

  const finalArgs = [MAIN, ...rest, '--weflow-config', configPath]
  if (mediaMap) finalArgs.push('--media-map', mediaMap)
  if (revokeMap) finalArgs.push('--revoke-map', revokeMap)
  const res = run(process.execPath, finalArgs, { allowNonZero: true })
  if (revokeMap) rmSync(revokeMap, { force: true })
  if ((res.status ?? 0) === 0 || res.status === 5) {
    if (!values.noAudit) {
      const output = outputDirFromArgs(rest)
      if (output) {
        console.error('完整性校验（数据库 → 原始 JSON → 导出 JSONL）...')
        // Capture stdout so `--json` exports keep a clean stdout.
        const audit = run(python, [
          AUDIT,
          '--output', resolve(output),
          '--weflow-cli-dir', scriptsDir,
          '--config', configPath,
        ], {
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
          capture: true,
        })
        if (audit.stdout) process.stderr.write(audit.stdout)
        if (audit.stderr) process.stderr.write(audit.stderr)
        if (audit.status !== 0) process.exit(audit.status ?? 5)
      }
    }
  }
  process.exit(res.status ?? 0)
}

function outputDirFromArgs(args) {
  const list = args.map(String)
  for (let i = 0; i < list.length; i += 1) {
    if (list[i] === '--output' || list[i] === '-o') return list[i + 1] || ''
    if (list[i].startsWith('--output=')) return list[i].slice('--output='.length)
  }
  return ''
}

main()
