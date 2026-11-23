import { promises as fs } from 'node:fs'
import { basename, extname, join, relative } from 'node:path'
import {
  TYPE_LABELS,
  buildText,
  classifyMessage,
  messageKey,
  messageText,
  linkFromMessage,
  normalizeLocalType,
  resolveSender,
  parseVideoMeta,
} from './parse.mjs'
import {
  findVideoPoster,
  resolveVideo,
  verifyVideoFile,
  videoExtension,
} from './video.mjs'
import {
  linkOrCopy,
  pathExists,
  pathToPosix,
  safeName,
} from './media.mjs'
import { isPathInside } from './config.mjs'
import { localDateOf, toIso, toLocalDateTime } from './time.mjs'
import { renderHtml } from './html.mjs'
import { renderMarkdown } from './markdown.mjs'

function readManifestEntry(mediaManifest, key) {
  const raw = mediaManifest?.records?.[key]
  if (!raw) return null
  if (typeof raw === 'string') return { path: raw, kind: 'image' }
  if (typeof raw !== 'object' || !raw.path) return null
  return raw
}

function uniqueName(desired, used, fallback) {
  const original = safeName(desired || fallback || 'file')
  if (!used.has(original)) return original
  const ext = extname(original)
  const stem = ext ? original.slice(0, -ext.length) : original
  for (let index = 2; index < 10_000; index += 1) {
    const candidate = `${stem}_${index}${ext}`
    if (!used.has(candidate)) return candidate
  }
  return `${stem}_${Date.now()}${ext}`
}

async function linkMediaFile({ source, subdir, desiredName, bundleDir, used, copy = false }) {
  if (!source || !(await pathExists(source))) return null
  let stat
  try {
    stat = await fs.stat(source)
  } catch {
    return null
  }
  if (!stat.isFile()) return null

  const dir = join(bundleDir, 'media', subdir)
  await fs.mkdir(dir, { recursive: true })
  let name = uniqueName(desiredName, used)
  if (await pathExists(join(dir, name))) {
    const ext = extname(name)
    const stem = ext ? name.slice(0, -ext.length) : name
    for (let index = 2; index < 10_000; index += 1) {
      const candidate = `${stem}_${index}${ext}`
      if (!used.has(candidate) && !(await pathExists(join(dir, candidate)))) {
        name = candidate
        break
      }
    }
  }
  const target = join(dir, name)
  used.add(name)
  if (copy) {
    // Videos are linked straight from WeChat's msg/video. A hard link would
    // let an edit in the export mutate WeChat's source file, so copy instead.
    await fs.copyFile(source, target)
  } else {
    await linkOrCopy(source, target)
  }
  const rel = pathToPosix(relative(bundleDir, target))
  return { rel, bytes: stat.size, name }
}

function missingReason(mediaState, kind) {
  if (mediaState?.failed) return 'media-extraction-failed'
  if (mediaState?.skipped) return 'media-skipped'
  return `${kind}-not-local`
}

function addMissing(record, kind, reason) {
  if (record.missing.some((item) => item.kind === kind && item.reason === reason)) return
  record.missing.push({ kind, reason })
}

function normalizeForwardRecord(item) {
  const out = {}
  for (const key of [
    'datatype', 'kind', 'sender', 'time', 'text', 'title', 'size',
    'md5', 'thumb_md5', 'image_md5', 'video_md5',
  ]) {
    if (item?.[key] !== undefined) out[key] = item[key]
  }
  return out
}

async function buildRecord(message, options) {
  const { target, contactMap, meLabel, includeRaw, mediaManifest, mediaMap, revokeMap, videoIndex, mediaState, ownWxid } = options
  const classified = classifyMessage(message)
  if (!options.types.has(classified.type)) return null

  const senderInfo = classified.type === 'system'
    ? { senderId: '', sender: '系统', isMe: false }
    : resolveSender(message, {
      target,
      contactMap,
      meLabel,
      kind: target.kind,
      ownWxid,
    })
  const senderId = senderInfo.senderId || ''
  const text = buildText(message, classified, senderId)
  const timestamp = Number(message.createTime) || 0
  const record = {
    id: messageKey(message),
    seq: 0,
    localId: message.localId ?? null,
    serverId: message.serverId ?? null,
    timestamp,
    iso: timestamp ? toIso(timestamp) : '',
    localTime: timestamp ? toLocalDateTime(timestamp) : '',
    day: timestamp ? localDateOf(timestamp) : '',
    senderId,
    sender: senderInfo.sender,
    isMe: senderInfo.isMe,
    type: classified.type,
    typeLabel: TYPE_LABELS[classified.type] || classified.type,
    messageType: message.messageType || '',
    rawType: normalizeLocalType(message.localType),
    text,
    media: {},
    missing: [],
  }
  if (message.quote) record.quote = message.quote
  if (message.reply) record.reply = message.reply
  if (Array.isArray(message.records) && message.records.length) {
    // Keep only stable fields; never leak absolute paths from the work tree.
    record.records = message.records.map(normalizeForwardRecord)
    if (message.isNote) record.isNote = true
  }
  if (classified.type === 'transfer' && message.amount) {
    record.amount = message.amount
  }
  if (classified.type === 'redpacket') {
    record.redpacket = {
      title: message.appTitle || '',
      description: message.appDescription || '',
    }
  }
  if (classified.type === 'location') {
    record.location = {
      lat: message.lat || '',
      lng: message.lng || '',
      title: message.appTitle || '',
      city: message.appDescription || '',
      url: message.appUrl || '',
    }
  }
  if (classified.type === 'card' && message.cardUsername) {
    record.contact = {
      username: message.cardUsername,
      title: message.appTitle || '',
    }
  }
  if (includeRaw) record.rawContent = message.rawContent || message.content || ''

  const manifest = readManifestEntry(mediaManifest, record.id)
  const mapped = mediaMap?.get(record.id) || null
  const revoked = revokeMap?.get(record.id) || null
  if (revoked) {
    record.revoked = true
    record.revoke = {
      kind: revoked.kind || 'unknown',
      batchId: revoked.batch_id ?? null,
      msgUniqueId: revoked.msg_unique_id || '',
      revokeTime: revoked.revoke_time ?? null,
      serverId: revoked.server_id || '',
    }
  }
  const manifestPath = manifest?.path
  const source = manifestPath && isPathInside(options.rawDir, join(options.rawDir, manifestPath))
    ? join(options.rawDir, manifestPath)
    : null
  const mappedImage = mapped?.media && /\.(?:jpe?g|png|gif|webp)$/i.test(mapped.media)
    ? mapped.media
    : ''

  if (classified.type === 'image') {
    const imageSource = (manifest?.kind === 'image' && source) ? source : mappedImage
    if (imageSource) {
      const linked = await options.linkMedia(imageSource, 'images', basename(imageSource), record.localId)
      if (linked) {
        record.media.image = linked.rel
        options.stats.media.images.found += 1
        options.mediaEntries.push({ kind: 'image', path: linked.rel, messageId: record.id, bytes: linked.bytes })
      } else {
        addMissing(record, 'image', 'image-not-local')
        options.stats.media.images.missing += 1
      }
    } else {
      addMissing(record, 'image', missingReason(mediaState, 'image'))
      options.stats.media.images.missing += 1
    }
  } else if (classified.type === 'file') {
    const fileName = manifest?.name || classified.file?.title || mapped?.file_name || '未命名文件'
    const fileBytes = Number(manifest?.bytes) || classified.file?.bytes
    record.fileName = fileName
    record.fileSize = Number.isFinite(fileBytes) ? fileBytes : undefined
    const fileSource = (manifest?.kind === 'file' && source) ? source : (mapped?.media || '')
    if (fileSource) {
      const linked = await options.linkMedia(fileSource, 'files', fileName, record.localId)
      if (linked) {
        record.media.file = linked.rel
        record.fileName = fileName
        record.fileSize = linked.bytes
        record.text = `[文件] ${fileName}`
        options.stats.media.files.found += 1
        options.mediaEntries.push({ kind: 'file', path: linked.rel, messageId: record.id, bytes: linked.bytes, name: fileName })
      } else {
        addMissing(record, 'file', 'file-not-on-disk')
        options.stats.media.files.missing += 1
      }
    } else {
      addMissing(record, 'file', missingReason(mediaState, 'file'))
      options.stats.media.files.missing += 1
    }
  } else if (classified.type === 'video') {
    const meta = parseVideoMeta(messageText(message))
    const candidates = options.videoCandidates(message)
    let hit = null
    if (mapped?.media) hit = { file: mapped.media, key: mapped.video_md5 || candidates[0] || '' }
    else if (manifest?.kind === 'video' && source) hit = { file: source, key: manifest.md5 || candidates[0] || '' }
    else hit = resolveVideo(candidates, videoIndex)
    if (hit) {
      const ext = videoExtension(hit.file)
      const md5 = hit.key ? hit.key.replace(/_raw$/, '') : (mapped?.video_md5 || candidates[0])
      const linked = await options.linkMedia(
        hit.file,
        'videos',
        `video_${record.localId ?? 'x'}_${String(md5).slice(0, 12)}${ext}`,
        record.localId,
      )
      if (linked) {
        record.media.video = linked.rel
        record.videoMd5 = md5
        record.videoPlayLength = meta.playLength || undefined
        options.stats.media.videos.found += 1
        options.mediaEntries.push({ kind: 'video', path: linked.rel, messageId: record.id, bytes: linked.bytes, name: linked.name })
        const verified = verifyVideoFile(hit.file)
        if (!verified.ok) {
          addMissing(record, 'video', verified.reason || 'unrecognized-video-magic')
          options.stats.media.videos.warned += 1
        }
      } else {
        addMissing(record, 'video', 'video-not-local')
        options.stats.media.videos.missing += 1
      }
    } else {
      addMissing(record, 'video', options.videoRootsFound ? 'video-not-local' : 'video-root-not-found')
      options.stats.media.videos.missing += 1
    }

    // A poster is optional; prefer the 4.2 media map, then weflow's manifest,
    // then a file beside the mp4.
    let posterSource = mapped?.poster || null
    if (!posterSource && manifest?.poster) {
      const candidate = join(options.rawDir, manifest.poster)
      if (isPathInside(options.rawDir, candidate)) posterSource = candidate
    }
    if (!posterSource && manifest?.kind === 'image' && source) posterSource = source
    if (!posterSource) posterSource = findVideoPoster(candidates, hit?.file, videoIndex)
    if (posterSource) {
      const linked = await options.linkMedia(
        posterSource,
        'images',
        `poster_${record.localId ?? 'x'}_${String(candidates[0] || 'x').slice(0, 8)}${extname(posterSource) || '.jpg'}`,
        record.localId,
      )
      if (linked) {
        record.media.poster = linked.rel
        options.stats.media.posters.found += 1
        options.mediaEntries.push({ kind: 'poster', path: linked.rel, messageId: record.id, bytes: linked.bytes })
      }
    }
  } else if (classified.type === 'voice') {
    const voiceSource = mapped?.media || (manifest?.kind === 'voice' && source ? source : '')
    if (voiceSource) {
      const linked = await options.linkMedia(
        voiceSource,
        'voice',
        `voice_${record.localId ?? 'x'}_${record.timestamp}${extname(voiceSource) || '.wav'}`,
        record.localId,
      )
      if (linked) {
        record.media.voice = linked.rel
        options.stats.media.voices.found += 1
        options.mediaEntries.push({ kind: 'voice', path: linked.rel, messageId: record.id, bytes: linked.bytes })
      } else {
        addMissing(record, 'voice', 'voice-not-local')
        options.stats.media.voices.missing += 1
      }
    } else {
      addMissing(record, 'voice', 'voice-not-local')
      options.stats.media.voices.missing += 1
    }
  } else if (classified.type === 'emoji') {
    // A sticker is either a media-map entry (4.2 sticker map: the decrypted
    // PNG/GIF) or weflow's own extracted image for the 4.1 path.
    const stickerSource = mapped?.media || ((manifest?.kind === 'image' || manifest?.kind === 'emoji' || manifest?.kind === 'sticker') ? source : '')
    if (stickerSource) {
      const ext = extname(stickerSource) || '.png'
      // Name it after the sticker id when known: the same sticker is often
      // sent by several people, and the id keeps the name stable.
      const stem = mapped?.sticker_md5
        ? `sticker_${String(mapped.sticker_md5).slice(0, 12)}`
        : `sticker_${record.localId ?? 'x'}`
      const linked = await options.linkMedia(stickerSource, 'stickers', `${stem}${ext}`, record.localId)
      if (linked) {
        record.media.sticker = linked.rel
        if (mapped?.sticker_md5) record.stickerMd5 = mapped.sticker_md5
        options.stats.media.emojis.found += 1
        options.mediaEntries.push({ kind: 'emoji', path: linked.rel, messageId: record.id, bytes: linked.bytes })
      } else {
        addMissing(record, 'emoji', 'sticker-not-local')
        options.stats.media.emojis.missing += 1
      }
    } else {
      addMissing(record, 'emoji', options.stickersEnabled === false
        ? 'sticker-map-disabled'
        : 'sticker-not-local')
      options.stats.media.emojis.missing += 1
    }
  } else if (classified.type === 'link' || classified.type === 'card') {
    const link = linkFromMessage(message)
    if (link) {
      record.link = link
      if (link.title) record.text = link.title
      else if (link.url) record.text = link.url
    }
    if (manifest?.kind === 'image' && source) {
      const linked = await options.linkMedia(source, 'images', basename(source), record.localId)
      if (linked) {
        record.media.image = linked.rel
        options.mediaEntries.push({ kind: 'image', path: linked.rel, messageId: record.id, bytes: linked.bytes })
      }
    }
  }

  return record
}

function summarize(records) {
  const stats = {
    totalMessages: 0,
    exportedMessages: records.length,
    skippedByType: 0,
    byType: {},
    media: {
      images: { found: 0, missing: 0 },
      files: { found: 0, missing: 0 },
      videos: { found: 0, missing: 0, warned: 0 },
      voices: { found: 0, missing: 0 },
      posters: { found: 0 },
      emojis: { found: 0, missing: 0 },
    },
    revoked: 0,
    senders: 0,
    firstMessage: null,
    lastMessage: null,
  }
  const senders = new Set()
  for (const record of records) {
    stats.byType[record.type] = (stats.byType[record.type] || 0) + 1
    if (record.revoked) stats.revoked += 1
    if (record.sender) senders.add(record.sender)
    if (!stats.firstMessage || record.timestamp < stats.firstMessage.timestamp) {
      stats.firstMessage = { timestamp: record.timestamp, localTime: record.localTime }
    }
    if (!stats.lastMessage || record.timestamp > stats.lastMessage.timestamp) {
      stats.lastMessage = { timestamp: record.timestamp, localTime: record.localTime }
    }
  }
  stats.senders = senders.size
  return stats
}

function buildReadme({ target, manifest }) {
  const stats = manifest.stats
  const lines = [
    '微信聊天记录导出',
    '================',
    '',
    `目标：${target.name || target.id}（${target.kind === 'group' ? '群聊' : '个人'}）`,
    `会话 ID：${target.id}`,
    `时间范围：${manifest.query.label}`,
    `导出时间：${manifest.generatedAt}`,
    `消息数：${stats.exportedMessages}`,
    '',
    '文件说明：',
    '- index.html      可直接双击打开的聊天浏览页（支持按类型/发送者/关键词筛选）',
    '- transcript.md   按日期分组的 Markdown 转写',
    '- messages.jsonl  一行一条消息的机器可读格式（适合喂给脚本/模型）',
    '- messages.json   同上，JSON 数组形式',
    '- manifest.json   查询范围、统计、缺失媒体原因',
    '- media/          图片、文件本体、视频',
    '  - images/      图片与视频封面',
    '  - files/       聊天里传输的文档（保留原文件名）',
    '  - videos/      聊天里的视频（mp4 等）',
    '- media/index.json 媒体文件清单',
    '',
    '说明：',
    `- 图片：已导出 ${stats.media.images.found}，缺失 ${stats.media.images.missing}`,
    `- 文件：已导出 ${stats.media.files.found}，缺失 ${stats.media.files.missing}`,
    `- 视频：已导出 ${stats.media.videos.found}，缺失 ${stats.media.videos.missing}`,
    `- 语音：已导出 ${stats.media.voices?.found ?? 0}，缺失 ${stats.media.voices?.missing ?? 0}`,
    ...(stats.revoked ? [`- 已标记撤回：${stats.revoked} 条（见 revokes.json）`] : []),
    '- “缺失”表示源数据不在本机（从未下载或被微信清理）或抽取失败，不是路径写错。',
    '',
    '隐私提示：',
    '- 本目录包含真实聊天内容与第三方个人信息，只在本机使用。',
    '- 不要提交到 Git、网盘或公开链接；交付他人前先脱敏并确认授权。',
    '- 不需要时可整个目录删除。',
    '',
  ]
  return lines.join('\n')
}

export async function buildTargetBundle(options) {
  const {
    target,
    rawMessages,
    rawDir,
    mediaManifest,
    mediaMap,
    revokeMap,
    videoIndex,
    contactMap,
    bundleDir,
    types,
    mediaState,
    warnings,
    meta,
  } = options

  await fs.mkdir(join(bundleDir, 'media', 'images'), { recursive: true })
  await fs.mkdir(join(bundleDir, 'media', 'files'), { recursive: true })
  await fs.mkdir(join(bundleDir, 'media', 'videos'), { recursive: true })

  const usedNames = {
    images: new Set(), files: new Set(), videos: new Set(), voice: new Set(), stickers: new Set(),
  }
  const sourceCache = new Map()
  const mediaEntries = []
  const stats = {
    totalMessages: Array.isArray(rawMessages) ? rawMessages.length : 0,
    exportedMessages: 0,
    skippedByType: 0,
    byType: {},
    media: {
      images: { found: 0, missing: 0 },
      files: { found: 0, missing: 0 },
      videos: { found: 0, missing: 0, warned: 0 },
      voices: { found: 0, missing: 0 },
      posters: { found: 0 },
      emojis: { found: 0, missing: 0 },
    },
    revoked: 0,
    senders: 0,
    firstMessage: null,
    lastMessage: null,
  }

  const records = []
  for (const message of rawMessages || []) {
    const record = await buildRecord(message, {
      target,
      contactMap,
      meLabel: options.meLabel || '我',
      ownWxid: options.ownWxid || '',
      includeRaw: Boolean(options.includeRaw),
      types,
      mediaManifest,
      mediaMap,
      revokeMap,
      videoIndex,
      mediaState,
      rawDir,
      stats,
      mediaEntries,
      videoCandidates: options.videoCandidates,
      videoRootsFound: options.videoRootsFound,
      stickersEnabled: options.stickersEnabled,
      linkMedia: (source, subdir, desiredName) => {
        // The same image/video often appears in several messages; link it once.
        const cacheKey = `${subdir}\u0000${source}`
        if (!sourceCache.has(cacheKey)) {
          sourceCache.set(cacheKey, linkMediaFile({
            source,
            subdir,
            desiredName,
            bundleDir,
            used: usedNames[subdir] || usedNames.images,
            copy: subdir === 'videos',
          }))
        }
        return sourceCache.get(cacheKey)
      },
    })
    if (!record) {
      stats.skippedByType += 1
      continue
    }
    records.push(record)
  }

  records.sort((a, b) => {
    if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp
    return String(a.localId).localeCompare(String(b.localId), 'en', { numeric: true })
  })
  records.forEach((record, index) => {
    record.seq = index + 1
  })

  const computed = summarize(records)
  computed.totalMessages = stats.totalMessages
  computed.skippedByType = stats.skippedByType
  computed.media = stats.media

  const manifest = {
    schema: 'wechat-export-bundle/v1',
    generatedAt: new Date().toISOString(),
    target: {
      id: target.id,
      name: target.name || target.id,
      kind: target.kind,
    },
    query: {
      label: options.queryLabel,
      date: options.queryDate || null,
      from: options.queryFrom || null,
      to: options.queryTo || null,
      types: [...types],
      limit: options.limit ?? 0,
    },
    source: {
      tool: 'weflow-cli',
      version: meta?.weflowVersion || null,
      mediaStrategy: meta?.mediaStrategy || 'none',
      mediaExtraction: mediaState || null,
      revokes: options.revokeReport
        ? { items: Object.keys(options.revokeReport.items || {}).length, orphans: (options.revokeReport.orphans || []).length }
        : null,
    },
    stats: computed,
    warnings: [...new Set(warnings || [])],
    files: {
      html: options.html ? 'index.html' : null,
      markdown: options.markdown ? 'transcript.md' : null,
      jsonl: 'messages.jsonl',
      json: 'messages.json',
      mediaIndex: 'media/index.json',
      revokes: null,
    },
  }

  const jsonl = records.map((record) => JSON.stringify(record)).join('\n') + (records.length ? '\n' : '')
  await fs.writeFile(join(bundleDir, 'messages.jsonl'), jsonl, 'utf8')
  await fs.writeFile(join(bundleDir, 'messages.json'), JSON.stringify({
    schema: 'wechat-export-messages/v1',
    generatedAt: manifest.generatedAt,
    target: manifest.target,
    query: manifest.query,
    stats: computed,
    messages: records,
  }, null, 2) + '\n', 'utf8')
  await fs.writeFile(join(bundleDir, 'media', 'index.json'), JSON.stringify({
    schema: 'wechat-export-media/v1',
    generatedAt: manifest.generatedAt,
    items: mediaEntries,
  }, null, 2) + '\n', 'utf8')

  // Revoke traces: batch entries point at an original message; single-revoke
  // rows without a local message are listed as orphans so their content is not
  // lost.
  if (options.revokeReport) {
    const ids = new Set(records.map((record) => record.id))
    const revokeItems = Object.entries(options.revokeReport.items || {})
      .filter(([, value]) => !value.target || value.target === target.id)
      .map(([id, value]) => ({ messageId: id, exported: ids.has(id), ...value }))
    const orphans = (options.revokeReport.orphans || [])
      .filter((item) => item.target === target.id)
    if (revokeItems.length || orphans.length) {
      await fs.writeFile(join(bundleDir, 'revokes.json'), JSON.stringify({
        schema: 'wechat-export-revokes/v1',
        generatedAt: manifest.generatedAt,
        target: manifest.target,
        counts: { items: revokeItems.length, orphans: orphans.length },
        items: revokeItems,
        orphans,
      }, null, 2) + '\n', 'utf8')
      manifest.files.revokes = 'revokes.json'
    }
  }
  await fs.writeFile(join(bundleDir, 'README.txt'), buildReadme({ target, manifest }), 'utf8')

  if (options.markdown) {
    await fs.writeFile(join(bundleDir, 'transcript.md'), renderMarkdown({ target, records, manifest }), 'utf8')
  }

  if (options.html) {
    const html = renderHtml({ target, records, manifest, htmlMaxMessages: options.maxHtmlMessages })
    if (html.skipped) {
      manifest.files.html = null
      manifest.warnings.push(html.reason)
      warnings.push(html.reason)
    } else {
      await fs.writeFile(join(bundleDir, 'index.html'), html, 'utf8')
    }
  }

  await fs.writeFile(join(bundleDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', 'utf8')
  return { manifest, records, mediaEntries }
}
