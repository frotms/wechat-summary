const PHONE = /(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)/g
const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g
const ID_CARD = /(?<!\d)\d{17}[\dXx](?!\d)/g
const BANK_CARD = /(?<!\d)\d{16,19}(?!\d)/g

export const REDACT_MODES = new Set(['none', 'basic', 'strict'])

export function normalizeRedactMode(value) {
  const mode = String(value || 'none').toLowerCase()
  return REDACT_MODES.has(mode) ? mode : ''
}

export function redactText(value, mode = 'basic') {
  let text = String(value ?? '')
  if (!text || mode === 'none') return text
  if (mode === 'strict') {
    // Order matters: mask the longer ID/card patterns before the generic
    // 16-19 digit bank-card rule.
    text = text.replace(ID_CARD, '[身份证]')
    text = text.replace(BANK_CARD, '[银行卡]')
  }
  text = text.replace(PHONE, '[手机号]')
  text = text.replace(EMAIL, '[邮箱]')
  return text
}

function redactRecordFields(record, mode) {
  if (!record || typeof record !== 'object') return
  if (typeof record.text === 'string') record.text = redactText(record.text, mode)
  if (typeof record.rawContent === 'string') record.rawContent = redactText(record.rawContent, mode)
  if (typeof record.reply === 'string') record.reply = redactText(record.reply, mode)
  if (record.quote && typeof record.quote.text === 'string') {
    record.quote.text = redactText(record.quote.text, mode)
  }
  if (record.miniprogram) {
    for (const key of ['title', 'description', 'pagePath']) {
      if (typeof record.miniprogram[key] === 'string') {
        record.miniprogram[key] = redactText(record.miniprogram[key], mode)
      }
    }
  }
  for (const item of record.records || []) {
    if (typeof item.text === 'string') item.text = redactText(item.text, mode)
    if (typeof item.title === 'string') item.title = redactText(item.title, mode)
  }
}

/** Redact a copy of the records; the original array is not mutated. */
export function redactRecords(records, mode) {
  if (!mode || mode === 'none') return records
  const output = records.map((record) => structuredClone(record))
  for (const record of output) redactRecordFields(record, mode)
  return output
}
