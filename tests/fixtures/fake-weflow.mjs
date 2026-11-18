#!/usr/bin/env node
/**
 * Minimal stand-in for weflow-cli used by the end-to-end test. It reads a
 * fixture JSON from FAKE_WEFLOW_FIXTURE and implements only the commands this
 * skill calls: --version, check, sessions, contacts, media, export json/md.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

const args = process.argv.slice(2)
const fixture = JSON.parse(readFileSync(process.env.FAKE_WEFLOW_FIXTURE, 'utf8'))

function arg(name, fallback) {
  const index = args.indexOf(name)
  return index >= 0 ? args[index + 1] : fallback
}

function print(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`)
}

function parseDate(text, endOfDay = false) {
  if (!text) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (match) {
    const [, y, m, d] = match
    return new Date(Number(y), Number(m) - 1, Number(d), endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0, endOfDay ? 999 : 0)
  }
  const ms = Date.parse(text)
  return Number.isNaN(ms) ? null : new Date(ms)
}

function filterMessages(messages) {
  const date = arg('--date')
  const from = parseDate(arg('--from'), false)
  const to = parseDate(arg('--to'), true)
  const dateStart = date ? parseDate(date, false) : null
  const dateEnd = date ? parseDate(date, true) : null
  let out = [...(messages || [])]
  if (dateStart && dateEnd) {
    out = out.filter((m) => m.createTime * 1000 >= dateStart.getTime() && m.createTime * 1000 <= dateEnd.getTime())
  }
  if (from) out = out.filter((m) => m.createTime * 1000 >= from.getTime())
  if (to) out = out.filter((m) => m.createTime * 1000 <= to.getTime())
  out.sort((a, b) => a.createTime - b.createTime || a.localId - b.localId)
  const limit = Number(arg('--limit', '0')) || 0
  if (limit > 0) out = out.slice(-limit)
  return out
}

function jpegBytes() {
  return Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0xff, 0xd9])
}

function pdfBytes() {
  return Buffer.from('%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n', 'utf8')
}

function writeAssets(talker, output, messages) {
  const assetsName = `${talker}_assets`
  const assetsDir = join(output, assetsName)
  mkdirSync(assetsDir, { recursive: true })
  mkdirSync(join(assetsDir, 'files'), { recursive: true })

  const manifestPath = join(assetsDir, 'manifest.json')
  const manifest = existsSync(manifestPath)
    ? JSON.parse(readFileSync(manifestPath, 'utf8'))
    : { schema: 'weflow-media-manifest/v2', messages: {}, documents: { written: 0, copied: 0, unavailable: 0 } }

  for (const message of messages) {
    const key = `${message.localId}:${message.createTime}`
    if (Number(message.localType) === 3) {
      const name = `img_${message.localId}_fake.jpg`
      writeFileSync(join(assetsDir, name), jpegBytes())
      manifest.messages[key] = { path: `${assetsName}/${name}`, kind: 'image' }
    } else if (Number(message.localType) === 43) {
      const name = `img_${message.localId}_poster.jpg`
      writeFileSync(join(assetsDir, name), jpegBytes())
      manifest.messages[key] = { path: `${assetsName}/${name}`, kind: 'image' }
    } else if (Number(message.localType) === 49 && String(message.rawContent || '').includes('<type>6</type>')) {
      const title = /<title>([\s\S]*?)<\/title>/i.exec(String(message.rawContent || ''))?.[1] || `file_${message.localId}.bin`
      const safeTitle = title.replace(/[<>:"/\\|?*]/g, '_')
      writeFileSync(join(assetsDir, 'files', safeTitle), pdfBytes())
      manifest.messages[key] = {
        path: `${assetsName}/files/${safeTitle}`,
        kind: 'file',
        name: safeTitle,
        bytes: pdfBytes().length,
      }
    }
  }
  manifest.documents.written = Object.values(manifest.messages).filter((item) => item.kind === 'file').length
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8')
  return { assetsName, manifest }
}

function exportJson(talker) {
  const messages = filterMessages(fixture.messages?.[talker])
  if (!messages.length) {
    print({ success: false, code: 'EXPORT_FAILED', error: '未找到消息' })
    process.exit(1)
  }
  const output = arg('--output', './output')
  mkdirSync(output, { recursive: true })
  let media = null
  if (args.includes('--media')) {
    const written = writeAssets(talker, output, messages)
    media = { assets: { dir: written.assetsName, items: Object.keys(written.manifest.messages).length } }
  }
  const path = join(output, `${talker}_messages.json`)
  writeFileSync(path, JSON.stringify({
    schema: 'weflow-message/v1',
    source: 'fake-weflow',
    generatedAt: new Date().toISOString(),
    coverage: {
      requestedLimit: Number(arg('--limit', '0')) || 0,
      returned: messages.length,
      mayHaveMore: false,
    },
    messages,
  }, null, 2), 'utf8')
  print({ success: true, format: 'json', contract: 'weflow-v1', path, count: messages.length, media })
}

function exportMd(talker) {
  const messages = filterMessages(fixture.messages?.[talker])
  if (!messages.length) {
    print({ success: false, code: 'EXPORT_FAILED', error: '未找到消息' })
    process.exit(1)
  }
  const output = arg('--output', './output')
  const { assetsName, manifest } = writeAssets(talker, output, messages)
  const mdPath = join(output, `${talker}_messages.md`)
  writeFileSync(mdPath, `# fake export\n\n${messages.length} messages\n`, 'utf8')
  print({
    success: true,
    format: 'markdown',
    path: mdPath,
    count: messages.length,
    media: { assets: { dir: assetsName, items: Object.keys(manifest.messages).length } },
  })
}

if (args.includes('--version')) {
  process.stdout.write('9.9.9\n')
  process.exit(0)
}

const command = args[0]
if (command === 'check') {
  print({
    success: true,
    schema: 'weflow-check/v1',
    runtime: { node: { available: true, version: process.versions.node }, python: { available: true, version: 'Python 3.12.0' } },
    dependencies: {
      required: { sqlcipher3: true, html2text: true, zstandard: true, cryptography: true },
      optional: {},
    },
    configuration: { initialized: true, messageDatabase: true },
    wechatClient: { running: true, version: '4.1.15.56', dataDirs: [], compatibility: { verdict: 'verified' } },
  })
  process.exit(0)
}

if (command === 'sessions') {
  print({ success: true, sessions: fixture.sessions })
  process.exit(0)
}

if (command === 'contacts') {
  print({ success: true, contacts: fixture.contacts })
  process.exit(0)
}

if (command === 'media') {
  print({ success: true, total: 0, rows: [] })
  process.exit(0)
}

if (command === 'export') {
  const talker = args[1]
  const format = args[2]
  if (format === 'json') exportJson(talker)
  else if (format === 'md') exportMd(talker)
  else {
    print({ success: false, error: `unsupported format: ${format}` })
    process.exit(1)
  }
  process.exit(0)
}

print({ success: false, error: `unsupported command: ${args.join(' ')}` })
process.exit(1)
