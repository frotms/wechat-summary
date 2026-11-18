import test from 'node:test'
import assert from 'node:assert/strict'
import {
  eachDate,
  resolveTimeWindow,
  toLocalDateString,
} from '../scripts/lib/time.mjs'
import { UsageError } from '../scripts/lib/errors.mjs'

test('resolveTimeWindow --days includes today', () => {
  const now = new Date(2026, 10, 16, 12, 0, 0)
  const window = resolveTimeWindow({ days: '3' }, now)
  assert.equal(window.fromDate, '2026-11-14')
  assert.equal(window.toDate, '2026-11-16')
  assert.equal(window.fromArg, '2026-11-14')
  assert.equal(window.toArg, '2026-11-16')
})

test('resolveTimeWindow --date uses the whole local day', () => {
  const window = resolveTimeWindow({ date: '2026-01-05' })
  assert.equal(window.dateArg, '2026-01-05')
  assert.ok(window.fromTs < window.toTs)
  const start = new Date(window.fromTs * 1000)
  const end = new Date(window.toTs * 1000)
  assert.equal(toLocalDateString(start), '2026-01-05')
  assert.equal(toLocalDateString(end), '2026-01-05')
  assert.equal(end.getHours(), 23)
})

test('resolveTimeWindow --to is inclusive of the whole day', () => {
  const window = resolveTimeWindow({ from: '2026-01-01', to: '2026-01-31' })
  const end = new Date(window.toTs * 1000)
  assert.equal(toLocalDateString(end), '2026-01-31')
  assert.equal(end.getHours(), 23)
})

test('conflicting date filters are rejected', () => {
  assert.throws(() => resolveTimeWindow({ date: '2026-01-05', from: '2026-01-01' }), UsageError)
  assert.throws(() => resolveTimeWindow({ days: '3', to: '2026-01-31' }), UsageError)
})

test('eachDate spans month boundaries', () => {
  assert.deepEqual(eachDate('2026-01-30', '2026-02-02'), [
    '2026-01-30', '2026-01-31', '2026-02-01', '2026-02-02',
  ])
})
