import test from 'node:test'
import assert from 'node:assert/strict'
import {
  classifyAccount,
  isOfficialAccount,
  isSystemAccount,
} from '../scripts/lib/config.mjs'

test('official accounts are gh_* only', () => {
  assert.equal(isOfficialAccount('gh_abcdef123456'), true)
  assert.equal(isOfficialAccount('GH_ABCDEF'), true)
  assert.equal(isOfficialAccount('25984982148673309@openim'), false)
  assert.equal(isOfficialAccount('wxid_abc'), false)
  assert.equal(isOfficialAccount('123@chatroom'), false)
  assert.equal(isOfficialAccount(''), false)
})

test('@openim is a system/other account, not a person or official account', () => {
  assert.equal(isSystemAccount('25984982148673309@openim'), true)
  assert.equal(classifyAccount('25984982148673309@openim'), 'other')
  assert.equal(isOfficialAccount('25984982148673309@openim'), false)
})

test('classifyAccount distinguishes groups, persons and official accounts', () => {
  assert.equal(classifyAccount('123@chatroom'), 'group')
  assert.equal(classifyAccount('gh_abcdef123456'), 'other')
  assert.equal(classifyAccount('wxid_abc'), 'person')
})
