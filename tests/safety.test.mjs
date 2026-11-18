import test from 'node:test'
import assert from 'node:assert/strict'
import { assertReadOnlyWeFlowArgs } from '../scripts/lib/weflow.mjs'

test('read-only commands are allowed', () => {
  for (const args of [
    ['--version'],
    ['check', '--json'],
    ['sessions', '--json', '--limit', '10'],
    ['contacts', '--json'],
    ['media', '--type', 'video', '--json'],
    ['export', '123@chatroom', 'json', '--media'],
    ['export', '123@chatroom', 'md'],
  ]) {
    assert.doesNotThrow(() => assertReadOnlyWeFlowArgs(args), args.join(' '))
  }
})

test('write / forward / edit commands are refused', () => {
  for (const command of [
    'send', 'forward', 'revoke', 'delete', 'edit', 'init', 'setup',
    'config', 'fav', 'assistant', 'mcp',
  ]) {
    assert.throws(
      () => assertReadOnlyWeFlowArgs([command, 'x']),
      (error) => error.code === 'WEFLOW_READ_ONLY_VIOLATION',
      command,
    )
  }
})

test('only json/md export formats are allowed', () => {
  assert.doesNotThrow(() => assertReadOnlyWeFlowArgs(['export', 'x', 'json']))
  assert.doesNotThrow(() => assertReadOnlyWeFlowArgs(['export', 'x', 'md']))
  for (const format of ['html', 'csv', '', 'send']) {
    assert.throws(
      () => assertReadOnlyWeFlowArgs(['export', 'x', format]),
      (error) => error.code === 'WEFLOW_READ_ONLY_VIOLATION',
    )
  }
})

test('empty and unknown flag invocations are refused', () => {
  assert.throws(() => assertReadOnlyWeFlowArgs([]), /只读/)
  assert.throws(() => assertReadOnlyWeFlowArgs(['--send']), /只读/)
})
