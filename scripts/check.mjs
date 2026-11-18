#!/usr/bin/env node
/**
 * Thin wrapper: `node scripts/check.mjs [--json]` is equivalent to
 * `node scripts/wechat-export.mjs --check [--json]`.
 */
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const script = join(here, 'wechat-export.mjs')
const result = spawnSync(process.execPath, [script, '--check', ...process.argv.slice(2)], {
  stdio: 'inherit',
  windowsHide: true,
})
process.exit(result.status ?? 1)
