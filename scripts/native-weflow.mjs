#!/usr/bin/env node
/**
 * weflow-cli-compatible shim backed by the native Python reader.
 *
 * The Node orchestrator (`wechat-export.mjs`) only needs a small command
 * surface: check / sessions / contacts / media / export. This shim forwards
 * those to `native-backend.py`, so Level 2 4.2 exports do not require a
 * globally installed weflow-cli.
 */
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const BACKEND = join(HERE, 'native-backend.py')

function python() {
  return process.env.WEFLOW_NATIVE_PYTHON
    || join(process.env.USERPROFILE || '', '.weflow-cli', 'venv', 'Scripts', 'python.exe')
}

const command = python()
if (!existsSync(command)) {
  console.error(`native backend: Python 不存在: ${command}`)
  process.exit(3)
}

const res = spawnSync(command, [BACKEND, ...process.argv.slice(2)], {
  stdio: 'inherit',
  windowsHide: true,
  env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
})
if (res.error) {
  console.error(`native backend: ${res.error.message}`)
  process.exit(4)
}
process.exit(res.status ?? 1)
