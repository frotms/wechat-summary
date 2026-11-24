import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from 'node:crypto'
import { createReadStream, createWriteStream, promises as fs } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { pipeline } from 'node:stream/promises'
import { once } from 'node:events'

const MAGIC = Buffer.from('WXEXP1\n', 'ascii')
const SALT_LEN = 16
const NONCE_LEN = 12
const TAG_LEN = 16
const SCHEMA = 'wechat-export-enc/v1'

async function listFiles(root, skipPath) {
  const out = []
  async function walk(dir) {
    const entries = await fs.readdir(dir, { withFileTypes: true })
    for (const entry of entries) {
      const abs = join(dir, entry.name)
      if (skipPath && resolve(abs) === resolve(skipPath)) continue
      if (entry.isDirectory()) {
        await walk(abs)
      } else if (entry.isFile()) {
        const stat = await fs.stat(abs)
        out.push({
          abs,
          rel: relative(root, abs).split('\\').join('/'),
          size: stat.size,
        })
      }
    }
  }
  await walk(root)
  out.sort((a, b) => a.rel.localeCompare(b.rel))
  return out
}

async function writeChunk(stream, chunk) {
  if (!stream.write(chunk)) await once(stream, 'drain')
}

function safeOutputPath(root, rel) {
  const path = resolve(root, rel)
  const prefix = resolve(root) + (process.platform === 'win32' ? '\\' : '/')
  if (path !== resolve(root) && !path.startsWith(prefix)) {
    throw new Error(`加密包内路径越界: ${rel}`)
  }
  return path
}

export async function encryptDirectory(inputDir, outputFile, password) {
  if (!password) throw new Error('缺少加密密码')
  const root = resolve(inputDir)
  const files = await listFiles(root, outputFile)
  if (!files.length) throw new Error(`目录为空，无法加密: ${root}`)
  const header = Buffer.from(JSON.stringify({
    schema: SCHEMA,
    createdAt: new Date().toISOString(),
    files: files.map((item) => ({ path: item.rel, size: item.size })),
  }), 'utf8')
  const headerLen = Buffer.alloc(4)
  headerLen.writeUInt32BE(header.length, 0)

  const salt = randomBytes(SALT_LEN)
  const nonce = randomBytes(NONCE_LEN)
  const key = scryptSync(password, salt, 32)
  const cipher = createCipheriv('aes-256-gcm', key, nonce)
  await fs.mkdir(dirname(resolve(outputFile)), { recursive: true })
  const out = createWriteStream(outputFile, { flags: 'w' })
  try {
    await writeChunk(out, MAGIC)
    await writeChunk(out, salt)
    await writeChunk(out, nonce)
    await writeChunk(out, cipher.update(headerLen))
    await writeChunk(out, cipher.update(header))
    for (const file of files) {
      const read = createReadStream(file.abs)
      for await (const chunk of read) await writeChunk(out, cipher.update(chunk))
    }
    await writeChunk(out, cipher.final())
    await writeChunk(out, cipher.getAuthTag())
    out.end()
    await once(out, 'finish')
  } catch (error) {
    out.destroy()
    await fs.rm(outputFile, { force: true }).catch(() => {})
    throw error
  }
  return { file: resolve(outputFile), files: files.length }
}

async function readExact(handle, length, position) {
  const buffer = Buffer.alloc(length)
  const { bytesRead } = await handle.read(buffer, 0, length, position)
  if (bytesRead !== length) throw new Error('加密包头不完整')
  return buffer
}

export async function decryptBundle(inputFile, outputDir, password, options = {}) {
  if (!password) throw new Error('缺少解密密码')
  const source = resolve(inputFile)
  const root = resolve(outputDir)
  const stat = await fs.stat(source)
  const handle = await fs.open(source, 'r')
  let temp = ''
  try {
    const magic = await readExact(handle, MAGIC.length, 0)
    if (!magic.equals(MAGIC)) throw new Error('不是 wechat-export 加密包')
    const salt = await readExact(handle, SALT_LEN, MAGIC.length)
    const nonce = await readExact(handle, NONCE_LEN, MAGIC.length + SALT_LEN)
    const tagOffset = stat.size - TAG_LEN
    const cipherStart = MAGIC.length + SALT_LEN + NONCE_LEN
    if (tagOffset <= cipherStart) throw new Error('加密包长度不正确')
    const tag = await readExact(handle, TAG_LEN, tagOffset)
    await handle.close()

    if (!options.force) {
      const entries = await fs.readdir(root).catch(() => [])
      if (entries.length) throw new Error(`输出目录非空: ${root}（加 force 覆盖）`)
    }
    await fs.mkdir(root, { recursive: true })
    temp = join(root, `.decrypt-${process.pid}-${Date.now()}.tmp`)

    const key = scryptSync(password, salt, 32)
    const decipher = createDecipheriv('aes-256-gcm', key, nonce)
    decipher.setAuthTag(tag)
    await pipeline(
      createReadStream(source, { start: cipherStart, end: tagOffset - 1 }),
      decipher,
      createWriteStream(temp, { flags: 'w' }),
    )

    const plain = await fs.open(temp, 'r')
    let fileCount = 0
    try {
      const headerLen = (await readExact(plain, 4, 0)).readUInt32BE(0)
      const header = JSON.parse((await readExact(plain, headerLen, 4)).toString('utf8'))
      if (header.schema !== SCHEMA || !Array.isArray(header.files)) {
        throw new Error('加密包头格式不正确')
      }
      let offset = 4 + headerLen
      for (const file of header.files) {
        const target = safeOutputPath(root, file.path)
        await fs.mkdir(dirname(target), { recursive: true })
        const dest = await fs.open(target, 'w')
        try {
          let remaining = Number(file.size) || 0
          const buffer = Buffer.allocUnsafe(1024 * 1024)
          while (remaining > 0) {
            const want = Math.min(buffer.length, remaining)
            const { bytesRead } = await plain.read(buffer, 0, want, offset)
            if (!bytesRead) throw new Error(`加密包内容截断: ${file.path}`)
            await dest.write(buffer, 0, bytesRead)
            offset += bytesRead
            remaining -= bytesRead
          }
        } finally {
          await dest.close()
        }
        fileCount += 1
      }
    } finally {
      await plain.close()
    }
    return { output: root, files: fileCount }
  } finally {
    await handle.close().catch(() => {})
    if (temp) await fs.rm(temp, { force: true }).catch(() => {})
  }
}
