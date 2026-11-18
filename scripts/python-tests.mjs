#!/usr/bin/env node
/** Run Python unit tests with the managed weflow-cli venv (or WEFLOW_PYTHON). */
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const candidates = process.env.WEFLOW_PYTHON
  ? [process.env.WEFLOW_PYTHON]
  : process.platform === 'win32'
    ? [join(homedir(), '.weflow-cli', 'venv', 'Scripts', 'python.exe'), 'python.exe']
    : [join(homedir(), '.weflow-cli', 'venv', 'bin', 'python'), 'python3']

const python = candidates.find((path) => !path.includes('\\') || existsSync(path))
if (!python) {
  console.error('找不到 Python；设置 WEFLOW_PYTHON 或运行 weflow-cli setup --yes')
  process.exit(3)
}
const res = spawnSync(python, ['-m', 'unittest', 'discover', '-s', 'tests/python', '-v'], {
  stdio: 'inherit',
  cwd: root,
  windowsHide: true,
  env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
})
process.exit(res.status ?? 1)
