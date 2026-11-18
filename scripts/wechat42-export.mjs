#!/usr/bin/env node
/**
 * WeChat 4.2 backend for the wechat-export skill.
 *
 * WeChat 4.2 no longer exposes a shared passphrase that weflow-cli can derive.
 * This wrapper uses the MIT-licensed TangGod991021/wechat-exporter project to
 * extract each DB's raw key, decrypts the DBs, re-encrypts them with one known
 * raw key+salt in a sandbox, and then runs the normal wechat-export pipeline
 * against that sandbox. Media directories are junctions to the real account.
 *
 * The user must explicitly authorize that upstream project before using this
 * script. It never uploads data and never writes to WeChat's original DBs.
 *
 * Usage:
 *   node scripts/wechat42-export.mjs setup --tool-dir <wechat-exporter> \
 *     --account-root "<xwechat_files>/<account>"
 *   node scripts/wechat42-export.mjs --target "群名" --days 7 --types text,image,file,video
 *   node scripts/wechat42-export.mjs search --query 1684X --from 2026-08-17 --to 2026-11-17
 *   node scripts/wechat42-export.mjs clean
 */
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { nowStamp } from './lib/time.mjs'
import { isPathInside } from './lib/config.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const MAIN = join(HERE, 'wechat-export.mjs')
const SANDBOX = join(HERE, 'wechat42-sandbox.py')
const MEDIA_MAP = join(HERE, 'wechat42-media-map.py')
const STICKER_MAP = join(HERE, 'wechat-sticker-map.py')
const STICKER_SEED = join(HERE, 'wechat-sticker-seed.py')
const REVOKE_MAP = join(HERE, 'wechat-revoke-map.py')
const AUDIT = join(HERE, 'wechat-audit.py')
const SEARCH = join(HERE, 'wechat42-search.py')
const VENDOR = join(HERE, 'vendor', 'wechat42')
const VENDOR_COMMIT = 'cc718ef14fd815c9d04c2035b53cee0ee0539e36'
const UPSTREAM_URL = 'https://github.com/TangGod991021/wechat-exporter.git'
const CONFIG_PATH = join(homedir(), '.wechat-export', 'wechat42.json')

function usage() {
  console.log(`WeChat 4.2 backend

用法:
  node scripts/wechat42-export.mjs setup --account-root <账号目录> [--use-vendor]
  node scripts/wechat42-export.mjs [正常的 wechat-export 参数]
  node scripts/wechat42-export.mjs search --query <关键词> [--from D --to D]
  node scripts/wechat42-export.mjs status
  node scripts/wechat42-export.mjs doctor
  node scripts/wechat42-export.mjs check-updates
  node scripts/wechat42-export.mjs audit --output <导出目录>
  node scripts/wechat42-export.mjs clean

setup:
  --tool-dir <目录>        可选：外部 wechat-exporter 源码目录（MIT）；缺省用内置 vendor
  --use-vendor             强制使用内置 vendor（忽略已有外部 toolDir）
  --account-root <目录>    微信账号目录（含 db_storage 与 msg）
  --base-dir <目录>        4.2 工作根目录（默认 ~/.wechat-export/wechat42，或已有配置）
  --venv-python <路径>     可选；默认 ~/.weflow-cli/venv/Scripts/python.exe
  --refresh                重建 sandbox

export:
  所有参数原样传给 scripts/wechat-export.mjs；额外支持：
  --refresh                先重建 sandbox
  --no-media-map           不生成视频/图片补全映射（只依赖 weflow manifest）
  --no-sticker-map         不生成表情包映射（表情只保留文字标签）
  --sticker-seed <值>      表情包解密种子（缺省自动探测：配置或微信进程内存）
  --no-revoke-map          不生成撤回记录映射
  --no-audit               导出后不跑完整性校验

clean:
  删除 sandbox/明文 work/home（含 sandbox 密钥配置），保留已导出的结果。

doctor:
  检查 4.2 配置、上游、sandbox、venv、weflow-cli 读库和 sessions；输出 JSON。

check-updates:
  比较当前上游/vendor commit 与 GitHub 最新 HEAD；只报告，不自动更新。

安全:
  sandbox/work/home 都含敏感数据；不导出时请执行 clean。
`)
}

function fail(message, code = 2) {
  console.error(`✗ ${message}`)
  process.exit(code)
}

function readConfig() {
  try {
    return JSON.parse(readFileSync(CONFIG_PATH, 'utf8'))
  } catch {
    return null
  }
}

function writeConfig(config) {
  mkdirSync(dirname(CONFIG_PATH), { recursive: true })
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf8')
}

function findVenvPython() {
  if (process.env.WEFLOW_PYTHON) return process.env.WEFLOW_PYTHON
  const candidates = process.platform === 'win32'
    ? [join(homedir(), '.weflow-cli', 'venv', 'Scripts', 'python.exe')]
    : [join(homedir(), '.weflow-cli', 'venv', 'bin', 'python')]
  return candidates.find((path) => existsSync(path)) || (process.platform === 'win32' ? 'python.exe' : 'python3')
}

function upstreamCommit(toolDir) {
  const res = spawnSync('git', ['-C', toolDir, 'rev-parse', 'HEAD'], { encoding: 'utf8', windowsHide: true })
  return res.status === 0 ? res.stdout.trim() : ''
}

function parseFlags(args) {
  const values = {}
  const rest = []
  for (let i = 0; i < args.length; i += 1) {
    const token = args[i]
    if (token === '--refresh') { values.refresh = true; continue }
    if (token === '--no-media-map') { values.noMediaMap = true; continue }
    if (token === '--no-sticker-map') { values.noStickerMap = true; continue }
    if (token === '--no-revoke-map') { values.noRevokeMap = true; continue }
    if (token === '--no-audit') { values.noAudit = true; continue }
    if (token === '--use-vendor') { values.useVendor = true; continue }
    if (['--tool-dir', '--account-root', '--base-dir', '--venv-python', '--config', '--sticker-seed'].includes(token)) {
      values[token.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = args[++i]
      continue
    }
    rest.push(token)
  }
  return { values, rest }
}

function run(command, args, options = {}) {
  const stdio = options.capture
    ? 'pipe'
    : (options.stdoutToStderr ? ['inherit', 'pipe', 'inherit'] : 'inherit')
  const res = spawnSync(command, args, {
    stdio,
    encoding: 'utf8',
    windowsHide: true,
    env: options.env || process.env,
    timeout: options.timeoutMs || 0,
  })
  if (res.error) fail(`执行失败: ${command}: ${res.error.message}`, 4)
  if (options.capture) return res
  if (options.stdoutToStderr && res.stdout) process.stderr.write(res.stdout)
  if (res.status !== 0 && !options.allowNonZero) process.exit(res.status ?? 4)
  return res
}

function runSetup(values, options = {}) {
  const out = options.stderr ? console.error : console.log
  const existing = readConfig()
  const external = values.useVendor
    ? ''
    : (values.toolDir
      ? resolve(values.toolDir)
      : (existing?.toolDir ? resolve(existing.toolDir) : ''))
  const externalValid = external && existsSync(join(external, 'app', 'core', 'keys.py'))
  const vendorValid = existsSync(join(VENDOR, 'keys.py'))
  if (!externalValid && !vendorValid) {
    fail('没有可用的 wechat-exporter：内置 vendor 缺失，且 --tool-dir 不是有效上游目录')
  }
  const toolDir = externalValid ? external : ''
  const accountRoot = values.accountRoot
    ? resolve(values.accountRoot)
    : (existing?.accountRoot ? resolve(existing.accountRoot) : '')
  if (!accountRoot || !existsSync(join(accountRoot, 'db_storage'))) fail('--account-root 必须包含 db_storage')
  const baseDir = resolve(values.baseDir || existing?.baseDir || join(homedir(), '.wechat-export', 'wechat42'))
  if (isPathInside(accountRoot, baseDir)) {
    fail('--base-dir 不能放在微信账号目录内（只读约束：sandbox/work 会写入）', 2)
  }
  const sandboxDir = join(baseDir, 'sandbox')
  const homeDir = join(baseDir, 'home')
  const workDir = join(baseDir, 'work')
  const venvPython = resolve(values.venvPython || existing?.venvPython || findVenvPython())
  const config = {
    schema: 'wechat-export/wechat42-config/v1',
    toolDir,
    accountRoot,
    dataRoot: join(accountRoot, 'db_storage'),
    baseDir,
    sandboxDir,
    homeDir,
    workDir,
    venvPython,
    upstreamCommit: externalValid ? (upstreamCommit(external) || VENDOR_COMMIT) : VENDOR_COMMIT,
    updatedAt: new Date().toISOString(),
  }
  writeConfig(config)
  out(`配置: ${CONFIG_PATH}`)
  out(`上游: ${config.upstreamCommit || '未知'}`)
  out('构建 sandbox（提取密钥 → 解密 → 重新加密）...')
  const sandboxArgs = [
    SANDBOX,
    '--data-root', config.dataRoot,
    '--account-root', accountRoot,
    '--work-dir', workDir,
    '--sandbox-dir', sandboxDir,
    '--home-dir', homeDir,
    '--venv-python', venvPython,
  ]
  if (toolDir) sandboxArgs.push('--tool-dir', toolDir)
  run(venvPython, sandboxArgs, {
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    stdoutToStderr: Boolean(options.stderr),
  })
  out('✓ sandbox 就绪')
  out(`下一步: node scripts/wechat42-export.mjs --target "群名/人名" --days 7`)
}

/**
 * Point weflow-cli at the Python interpreter without letting it probe.
 *
 * weflow-cli detects Python by spawning candidate interpreters with 5s/10s
 * timeouts, and when the probe times out it falls back to the Microsoft Store
 * stub - which reports "Python was not found". The sandbox HOME hides the
 * real venv, so the probe is the only way it can find one, and under load
 * (parallel ffmpeg sticker decoding) it times out.
 *
 * weflow-cli reads `~/.weflow-cli/python-cmd.json` first, so writing that
 * file makes the choice deterministic.
 */
function ensurePythonCache(config) {
  if (!config.venvPython || !existsSync(config.venvPython)) return
  const dir = join(config.homeDir, '.weflow-cli')
  const path = join(dir, 'python-cmd.json')
  try {
    mkdirSync(dir, { recursive: true })
    if (existsSync(path)) return
    writeFileSync(path, JSON.stringify({
      cmd: config.venvPython,
      at: new Date().toISOString(),
    }, null, 2), 'utf8')
  } catch {
    // A read-only home just means weflow-cli falls back to probing.
  }
}

function sandboxEnv(config) {
  const env = { ...process.env, USERPROFILE: config.homeDir, HOME: config.homeDir }
  const venvBin = process.platform === 'win32'
    ? join(homedir(), '.weflow-cli', 'venv', 'Scripts')
    : join(homedir(), '.weflow-cli', 'venv', 'bin')
  env.PATH = venvBin + delimiter + env.PATH
  env.PYTHONIOENCODING = 'utf-8'
  env.PYTHONUTF8 = '1'
  return env
}

function sandboxConfigPath(config) {
  return join(config.homeDir, '.weflow-cli', 'config.json')
}

function dryRunTargets(config, args) {
  const res = run(process.execPath, [MAIN, ...args, '--dry-run', '--json'], {
    env: sandboxEnv(config), capture: true, timeoutMs: 300_000,
  })
  if (res.status !== 0) return null
  try {
    return JSON.parse(res.stdout)
  } catch {
    return null
  }
}

function buildMediaMap(config, targets, time, outPath) {
  mkdirSync(dirname(outPath), { recursive: true })
  const from = time?.date || time?.from || ''
  const to = time?.date || time?.to || ''
  const merged = { schema: 'wechat42-media-map-merged/v1', items: {}, sources: [] }
  for (const target of targets) {
    const tmp = `${outPath}.${target.id.replace(/[^A-Za-z0-9_-]/g, '_')}.json`
    const res = run(config.venvPython, [
      MEDIA_MAP,
      ...(config.toolDir ? ['--tool-dir', config.toolDir] : []),
      '--work-dir', config.workDir,
      '--account-root', config.accountRoot,
      '--target', target.id,
      '--from', from,
      '--to', to,
      '--out', tmp,
    ], { env: sandboxEnv(config), capture: true, timeoutMs: 600_000 })
    if (res.status !== 0) {
      console.error(`  ! 媒体映射失败: ${target.name || target.id}`)
      const detail = String(res.stderr || res.stdout || '').trim()
      if (detail) console.error(`    ${detail.slice(-800)}`)
      continue
    }
    try {
      const data = JSON.parse(readFileSync(tmp, 'utf8'))
      for (const [key, value] of Object.entries(data.items || {})) merged.items[key] = value
      merged.sources.push({ target: target.id, counts: data.counts })
      rmSync(tmp, { force: true })
    } catch {
      console.error(`  ! 媒体映射不可读: ${target.name || target.id}`)
    }
  }
  if (!Object.keys(merged.items).length) return null
  writeFileSync(outPath, JSON.stringify(merged, null, 2), 'utf8')
  return merged
}

/**
 * Resolve the sticker seed once per export.
 *
 * The seed is a per-account constant that only lives in WeChat's process
 * memory, so it is scanned for once and then reused. A configured seed
 * (--sticker-seed) skips the scan entirely.
 */
function resolveStickerSeed(config, values) {
  if (values.stickerSeed) return String(values.stickerSeed)
  const res = run(config.venvPython, [
    STICKER_SEED,
    '--account-root', config.accountRoot,
    '--config', sandboxConfigPath(config),
  ], { env: sandboxEnv(config), capture: true, timeoutMs: 300_000 })
  if (res.status !== 0) {
    console.error('  ! 表情包种子探测失败；表情只保留文字标签')
    return ''
  }
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

function buildStickerMap(config, targets, time, outPath, seed) {
  mkdirSync(dirname(outPath), { recursive: true })
  const from = time?.date || time?.from || ''
  const to = time?.date || time?.to || ''
  const merged = { schema: 'wechat42-media-map-merged/v1', items: {}, sources: [] }
  const stickerDir = join(config.baseDir, '_stickers')
  for (const target of targets) {
    const tmp = `${outPath}.${target.id.replace(/[^A-Za-z0-9_-]/g, '_')}.json`
    const res = run(config.venvPython, [
      STICKER_MAP,
      '--work-dir', config.workDir,
      '--account-root', config.accountRoot,
      '--target', target.id,
      '--from', from,
      '--to', to,
      '--out', tmp,
      '--out-dir', stickerDir,
      ...(seed ? ['--seed', seed] : []),
    ], { env: sandboxEnv(config), capture: true, timeoutMs: 900_000 })
    if (res.status !== 0) {
      console.error(`  ! 表情映射失败: ${target.name || target.id}`)
      const detail = String(res.stderr || res.stdout || '').trim()
      if (detail) console.error(`    ${detail.slice(-800)}`)
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
  writeFileSync(outPath, JSON.stringify(merged, null, 2), 'utf8')
  return merged
}

function buildRevokeMap(config, targets, time, outPath) {
  mkdirSync(dirname(outPath), { recursive: true })
  const from = time?.date || time?.from || ''
  const to = time?.date || time?.to || ''
  const merged = {
    schema: 'wechat-revoke-map-merged/v1', items: {}, orphans: [], sources: [],
  }
  for (const target of targets) {
    const tmp = `${outPath}.${target.id.replace(/[^A-Za-z0-9_-]/g, '_')}.json`
    const res = run(config.venvPython, [
      REVOKE_MAP,
      '--work-dir', config.workDir,
      '--target', target.id,
      '--from', from,
      '--to', to,
      '--out', tmp,
    ], { env: sandboxEnv(config), capture: true, timeoutMs: 300_000 })
    if (res.status !== 0) {
      console.error(`  ! 撤回映射失败: ${target.name || target.id}`)
      const detail = String(res.stderr || res.stdout || '').trim()
      if (detail) console.error(`    ${detail.slice(-800)}`)
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
  writeFileSync(outPath, JSON.stringify(merged, null, 2), 'utf8')
  return merged
}

function runExport(values, args) {
  const config = readConfig()
  if (!config) fail('还没有配置 4.2 后端；先运行 setup', 3)
  if (isPathInside(config.accountRoot, config.baseDir)) {
    fail('baseDir 位于微信账号目录内（只读约束）；用 setup --base-dir 改到别处', 2)
  }
  const dryRun = args.includes('--dry-run')
  if (values.refresh || !existsSync(sandboxConfigPath(config))) {
    runSetup({ ...values, toolDir: config.toolDir, accountRoot: config.accountRoot, baseDir: config.baseDir }, { stderr: true })
  }
  // The plaintext work dir may have been removed by `clean`. Media/revoke map
  // generation needs it; rebuild on demand (normal exports can use the sandbox
  // without it). Dry-run skips the maps entirely.
  const needWork = !dryRun && (!values.noMediaMap || !values.noRevokeMap)
  if (needWork && !existsSync(config.workDir)) {
    console.error('明文 work/ 不存在，重建 sandbox 以生成媒体/撤回映射...')
    runSetup({ toolDir: config.toolDir, accountRoot: config.accountRoot, baseDir: config.baseDir }, { stderr: true })
  }

  const realHome = process.env.USERPROFILE || homedir()
  const hasOutput = args.includes('--output') || args.includes('-o')
    || args.some((arg) => String(arg).startsWith('--output='))
  const output = join(realHome, '.wechat-export', `${nowStamp()}-wechat42`)
  const forward = [...args]
  if (!hasOutput) forward.push('--output', output)

  let mediaMapPath = null
  let revokeMapPath = null
  const wantStickers = !values.noStickerMap && !values.noMediaMap
  if (!dryRun && (!values.noMediaMap || !values.noRevokeMap)) {
    ensurePythonCache(config)
    const plan = dryRunTargets(config, forward)
    if (plan?.targets?.length) {
      if (!values.noMediaMap) {
        const mapPath = join(config.baseDir, `media-map-${nowStamp()}.json`)
        const merged = buildMediaMap(config, plan.targets, plan.time, mapPath)
        if (merged) {
          mediaMapPath = mapPath
          console.error(`媒体映射: ${Object.keys(merged.items).length} 条`)
        }
        // Stickers live in a separate encrypted cache, so they need their own
        // map (and their own seed).
        if (wantStickers) {
          const seed = resolveStickerSeed(config, values)
          if (seed) {
            const stickerPath = join(config.baseDir, `sticker-map-${nowStamp()}.json`)
            const stickers = buildStickerMap(config, plan.targets, plan.time, stickerPath, seed)
            if (stickers) {
              const items = Object.keys(stickers.items).length
              if (mediaMapPath) {
                const current = JSON.parse(readFileSync(mediaMapPath, 'utf8'))
                Object.assign(current.items, stickers.items)
                writeFileSync(mediaMapPath, JSON.stringify(current, null, 2), 'utf8')
              } else {
                mediaMapPath = stickerPath
              }
              console.error(`表情映射: ${items} 条`)
            }
          }
        }
      }
      if (!values.noRevokeMap) {
        const revokePath = join(config.baseDir, `revoke-map-${nowStamp()}.json`)
        const revokes = buildRevokeMap(config, plan.targets, plan.time, revokePath)
        if (revokes) {
          revokeMapPath = revokePath
          console.error(`撤回映射: ${Object.keys(revokes.items).length} 条，孤立 ${revokes.orphans.length} 条`)
        }
      }
    }
  }

  const finalArgs = [MAIN, ...forward, '--weflow-config', sandboxConfigPath(config)]
  if (mediaMapPath) finalArgs.push('--media-map', mediaMapPath)
  if (revokeMapPath) finalArgs.push('--revoke-map', revokeMapPath)
  const res = run(process.execPath, finalArgs, { env: sandboxEnv(config), allowNonZero: true })
  // The maps only bridge metadata into this one export; they contain local
  // paths, so don't leave copies behind.
  if (mediaMapPath) rmSync(mediaMapPath, { force: true })
  if (revokeMapPath) rmSync(revokeMapPath, { force: true })

  if ((res.status ?? 0) === 0 || res.status === 5) {
    if (!values.noAudit) {
      const outputArg = outputDirFromArgs(forward) || output
      if (existsSync(config.workDir)) {
        console.error('完整性校验（数据库 → 原始 JSON → 导出 JSONL）...')
        // Capture stdout so `--json` exports keep a clean machine-readable
        // stdout; the audit summary goes to stderr instead.
        const audit = run(config.venvPython, [AUDIT, '--output', outputArg, '--work-dir', config.workDir], {
          env: sandboxEnv(config), capture: true,
        })
        if (audit.stdout) process.stderr.write(audit.stdout)
        if (audit.stderr) process.stderr.write(audit.stderr)
        if (audit.status !== 0) process.exit(audit.status ?? 5)
      } else {
        console.error('提示: work/ 不存在，跳过完整性校验；重建后可跑 audit')
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

function runAudit(args) {
  const config = readConfig()
  if (!config) fail('还没有配置 4.2 后端；先运行 setup', 3)
  let output = ''
  let workDir = config.workDir
  let json = false
  for (let i = 0; i < args.length; i += 1) {
    const token = args[i]
    if (token === '--output' || token === '-o') { output = args[++i] || ''; continue }
    if (token.startsWith('--output=')) { output = token.slice('--output='.length); continue }
    if (token === '--work-dir') { workDir = args[++i] || ''; continue }
    if (token === '--json') { json = true; continue }
    fail(`未知的 audit 参数: ${token}`)
  }
  if (!output) fail('audit 需要 --output <导出目录>')
  if (!workDir || !existsSync(workDir)) {
    fail('明文 work/ 不存在；先运行 setup（或一次普通导出会重建）', 3)
  }
  const pythonArgs = [AUDIT, '--output', resolve(output), '--work-dir', resolve(workDir)]
  if (json) pythonArgs.push('--json')
  const res = run(config.venvPython, pythonArgs, { env: sandboxEnv(config) })
  process.exit(res.status ?? 0)
}

function parseSearchFlags(args) {
  const values = {}
  const rest = []
  const takesValue = new Set(['--query', '--from', '--to', '--out', '--max-sessions', '--max-matches'])
  for (let i = 0; i < args.length; i += 1) {
    const token = args[i]
    if (takesValue.has(token)) {
      values[token.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = args[++i]
      continue
    }
    rest.push(token)
  }
  return { values, rest }
}

function runSearch(values) {
  const config = readConfig()
  if (!config) fail('还没有配置 4.2 后端；先运行 setup', 3)
  if (!existsSync(config.workDir)) {
    console.error('明文 work/ 已清理，重建 sandbox 以支持搜索...')
    runSetup({ toolDir: config.toolDir, accountRoot: config.accountRoot, baseDir: config.baseDir }, { stderr: true })
  }
  const query = values.query
  if (!query) fail('search 需要 --query <关键词>')
  const out = values.out || join(config.baseDir, `search_${query}_${nowStamp()}.json`)
  const args = [
    SEARCH,
    ...(config.toolDir ? ['--tool-dir', config.toolDir] : []),
    '--work-dir', config.workDir,
    '--query', query,
    '--out', out,
  ]
  if (values.from) args.push('--from', values.from)
  if (values.to) args.push('--to', values.to)
  if (values.maxSessions) args.push('--max-sessions', values.maxSessions)
  if (values.maxMatches) args.push('--max-matches', values.maxMatches)
  const res = run(config.venvPython, args, { env: sandboxEnv(config) })
  process.exit(res.status ?? 0)
}

function runCheckUpdates() {
  const config = readConfig()
  if (!config) fail('还没有配置 4.2 后端；先运行 setup', 3)
  const source = config.toolDir ? 'external' : 'vendor'
  let current = VENDOR_COMMIT
  if (config.toolDir) {
    const res = spawnSync('git', ['-C', config.toolDir, 'rev-parse', 'HEAD'], {
      encoding: 'utf8', windowsHide: true, timeout: 20_000,
    })
    if (res.status === 0) current = res.stdout.trim()
  }
  const remote = spawnSync('git', ['ls-remote', UPSTREAM_URL, 'HEAD'], {
    encoding: 'utf8', windowsHide: true, timeout: 30_000,
  })
  let latest = ''
  if (remote.status === 0) latest = String(remote.stdout || '').trim().split(/\s+/)[0] || ''
  const result = {
    success: true,
    source,
    toolDir: config.toolDir || '(vendor)',
    current,
    latest: latest || null,
    updateAvailable: Boolean(latest && latest !== current),
    note: latest
      ? (latest !== current
        ? '上游有新 commit；在 staging 验证后再更新 vendor/config'
        : '当前已是最新上游 commit')
      : '无法访问 GitHub；只报告本地 commit',
    next: latest && latest !== current
      ? ['git clone --depth 1 ' + UPSTREAM_URL + ' <staging-dir>', '更新 scripts/vendor/wechat42 或 config.toolDir', 'node scripts/wechat42-export.mjs doctor']
      : [],
  }
  console.log(JSON.stringify(result, null, 2))
}

function runStatus() {
  const config = readConfig()
  if (!config) { console.log('未配置 4.2 后端'); return }
  console.log(JSON.stringify({
    config: CONFIG_PATH,
    toolDir: config.toolDir || '(vendor)',
    accountRoot: config.accountRoot,
    baseDir: config.baseDir,
    upstreamCommit: config.upstreamCommit,
    sandboxConfig: existsSync(sandboxConfigPath(config)),
    workDir: existsSync(config.workDir),
    venvPython: existsSync(config.venvPython) ? config.venvPython : `${config.venvPython} (missing)`,
  }, null, 2))
}

function runDoctor() {
  const config = readConfig()
  if (!config) fail('还没有配置 4.2 后端；先运行 setup', 3)
  const checks = {
    toolDir: config.toolDir
      ? existsSync(join(config.toolDir, 'app', 'core', 'keys.py'))
      : existsSync(join(VENDOR, 'keys.py')),
    sandboxConfig: existsSync(sandboxConfigPath(config)),
    workDir: existsSync(config.workDir),
    venvPython: existsSync(config.venvPython),
  }
  let compat = null
  try {
    compat = JSON.parse(readFileSync(join(HERE, '..', 'compat.json'), 'utf8'))
  } catch {
    compat = null
  }

  const env = sandboxEnv(config)
  let check = null
  let sessions = null
  try {
    const res = run(process.execPath, [MAIN, '--check', '--json'], { env, capture: true, timeoutMs: 180_000 })
    check = JSON.parse(res.stdout)
  } catch (error) {
    check = { error: String(error?.message || error) }
  }
  try {
    const res = run(process.execPath, [MAIN, '--list', '--json'], { env, capture: true, timeoutMs: 180_000 })
    sessions = JSON.parse(res.stdout)
  } catch (error) {
    sessions = { error: String(error?.message || error) }
  }

  const ready = Boolean(
    checks.toolDir && checks.sandboxConfig && checks.venvPython
    && check?.ready === true && sessions?.success === true,
  )
  const result = {
    success: ready,
    readOnly: {
      enforced: true,
      weflowCommands: ['check', 'sessions', 'contacts', 'media', 'export'],
      exportFormats: ['json', 'md'],
      forbidden: ['send', 'forward', 'delete', 'edit', 'revoke', 'config', 'fav', 'assistant'],
      writes: ['导出目录', '临时 work/sandbox/home（均在微信账号目录之外）'],
    },
    config: CONFIG_PATH,
    upstreamCommit: config.upstreamCommit,
    checks,
    weflow: check?.check || check,
    sessions: sessions?.count ?? null,
    workDirMissingImpact: checks.workDir ? null : '媒体映射/视频/语音/搜索需要明文 work，会在下次运行时重建',
    compat: compat?.tested?.find((item) => item.wechat === check?.check?.wechatVersion) || null,
    hints: [],
  }
  if (!checks.toolDir) result.hints.push('wechat-exporter 目录不存在；重新 clone 或修正 config.toolDir')
  if (!checks.sandboxConfig) result.hints.push('sandbox 配置不存在；运行 setup')
  if (!checks.venvPython) result.hints.push('venv Python 不存在；运行 weflow-cli setup --yes')
  if (check?.ready !== true) result.hints.push('weflow-cli 读库未就绪；查看 check 输出')
  if (sessions?.success !== true) result.hints.push('sessions 读取失败；查看 check/sessions 输出')
  console.log(JSON.stringify(result, null, 2))
  if (!ready) process.exitCode = 3
}

function runClean() {
  const config = readConfig()
  if (!config) { console.log('未配置 4.2 后端'); return }
  for (const dir of [config.sandboxDir, config.workDir, config.homeDir]) {
    if (existsSync(dir)) {
      rmSync(dir, { recursive: true, force: true })
      console.log(`已删除: ${dir}`)
    }
  }
  if (existsSync(config.baseDir)) {
    const maps = readdirSync(config.baseDir).filter((name) => /^(?:media|sticker|revoke)-map-.*\.json$/.test(name))
    for (const name of maps) rmSync(join(config.baseDir, name), { force: true })
    if (maps.length) console.log(`已删除: ${maps.length} 个媒体映射临时文件`)
  }
  console.log('已保留配置与导出结果；下次导出会重新构建 sandbox。')
}

function main() {
  const argv = process.argv.slice(2)
  const command = argv[0]
  if (command === '-h' || command === '--help' || command === 'help') { usage(); return }
  if (command === 'setup') { runSetup(parseFlags(argv.slice(1)).values); return }
  if (command === 'status') { runStatus(); return }
  if (command === 'doctor') { runDoctor(); return }
  if (command === 'check-updates') { runCheckUpdates(); return }
  if (command === 'audit') { runAudit(argv.slice(1)); return }
  if (command === 'clean') { runClean(); return }
  if (command === 'search') {
    const { values } = parseSearchFlags(argv.slice(1))
    runSearch(values)
    return
  }
  const { values, rest } = parseFlags(argv)
  runExport(values, rest)
}

main()
