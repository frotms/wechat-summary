const XML_ENTITIES = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&apos;': "'",
  '&#39;': "'",
  '&nbsp;': ' ',
}

export function decodeXmlEntities(text) {
  return String(text ?? '')
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => {
      try {
        return String.fromCodePoint(parseInt(hex, 16))
      } catch {
        return _
      }
    })
    .replace(/&#(\d+);/g, (_, dec) => {
      try {
        return String.fromCodePoint(Number(dec))
      } catch {
        return _
      }
    })
    .replace(/&(?:amp|lt|gt|quot|apos|nbsp|#39);/g, (entity) => XML_ENTITIES[entity] ?? entity)
}

function normalizeXml(text) {
  let value = String(text ?? '')
  if (value.includes('&lt;') && value.includes('&gt;')) {
    const decoded = decodeXmlEntities(value)
    if (decoded.includes('<msg') || decoded.includes('<appmsg')) value = decoded
  }
  return value
}

function firstTag(xml, tag) {
  const match = new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, 'i').exec(xml)
  return match ? decodeXmlEntities(match[1]).trim() : ''
}

function firstAttr(xml, name) {
  const match = new RegExp(`\\b${name}\\s*=\\s*["']([^"']*)["']`, 'i').exec(xml)
  return match ? decodeXmlEntities(match[1]).trim() : ''
}

/** rawContent is the usual XML carrier, but some rows only fill content. */
export function messageText(message) {
  return [message?.rawContent, message?.content].filter(Boolean).join('\n')
}

export function normalizeLocalType(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return 0
  if (n <= 0xffffffff) return Math.trunc(n)
  try {
    return Number(BigInt(Math.trunc(n)) & 0xffffffffn)
  } catch {
    return Math.trunc(n)
  }
}

/**
 * File messages are appmsg `type=6`. `<refermsg>` sits inside `<appmsg>`, so
 * only the first appmsg block is inspected (the same trap weflow-cli fixed).
 */
export function parseFileAppmsg(rawContent) {
  const xml = normalizeXml(rawContent)
  if (!xml || !xml.includes('<appmsg')) return null
  const match = /<appmsg\b[^>]*>([\s\S]*?)<\/appmsg>/i.exec(xml)
  if (!match) return null
  // Quoted messages embed a <refermsg> inside <appmsg>; its <type>6</type>
  // belongs to the quoted file, not to this message.
  const body = match[1].replace(/<refermsg\b[\s\S]*?<\/refermsg>/gi, '')
  const type = firstTag(body, 'type')
  if (String(type) !== '6') return null
  const title = firstTag(body, 'title')
  const md5 = firstTag(body, 'md5') || firstAttr(body, 'md5')
  const total = firstTag(body, 'totallen') || firstTag(body, 'total_len')
  const bytes = Number(total)
  return {
    isFile: true,
    title: title || '未命名文件',
    md5: md5 || '',
    bytes: Number.isFinite(bytes) && bytes >= 0 ? bytes : undefined,
  }
}

export function parseVideoMeta(rawContent) {
  const xml = normalizeXml(rawContent)
  if (!xml || !xml.includes('<videomsg')) return { playLength: '', md5: '', rawMd5: '' }
  return {
    playLength: firstAttr(xml, 'playlength') || firstAttr(xml, 'length'),
    md5: firstAttr(xml, 'md5'),
    rawMd5: firstAttr(xml, 'rawmd5'),
  }
}


/**
 * Mini-program / cloud-document appmsg (subtype 33/36). These are localType 49
 * but must not be rendered as ordinary links: the useful fields live inside
 * <weappinfo>.
 */
export function parseMiniProgramMeta(rawContent) {
  const xml = normalizeXml(rawContent)
  if (!xml || !xml.includes('<appmsg')) return null
  const appmsg = /<appmsg[^>]*>([\s\S]*?)<\/appmsg>/i.exec(xml)
  if (!appmsg) return null
  const body = appmsg[1]
  const appType = firstTag(body, 'type')
  if (!['33', '36'].includes(appType)) return null
  const weapp = /<weappinfo[^>]*>([\s\S]*?)<\/weappinfo>/i.exec(body)
  const meta = weapp ? weapp[1] : ''
  const pick = (tag) => firstTag(meta, tag) || firstTag(body, tag)
  return {
    appType,
    title: firstTag(body, 'title'),
    description: firstTag(body, 'des'),
    appName: pick('appname') || firstTag(body, 'sourcedisplayname') || firstTag(body, 'source'),
    appId: pick('appid') || firstAttr(body, 'appid'),
    pagePath: pick('pagepath'),
    username: pick('username'),
    iconUrl: pick('iconurl'),
    url: firstTag(body, 'url'),
  }
}

export function parseLinkMeta(rawContent) {
  const xml = normalizeXml(rawContent)
  if (!xml) return null
  const appmsg = /<appmsg\b[^>]*>([\s\S]*?)<\/appmsg>/i.exec(xml)
  const body = appmsg ? appmsg[1] : xml
  const url = firstTag(body, 'url') || firstAttr(body, 'url')
  const title = firstTag(body, 'title')
  const description = firstTag(body, 'des')
  if (!url && !title) return null
  return {
    url: /^https?:\/\//i.test(url) ? url : '',
    title: title || '',
    description: description || '',
  }
}

/**
 * Link/card messages: prefer the XML, then weflow-cli's appUrl/appTitle
 * fields. Some 4.2 rows have an empty rawContent but a populated appUrl.
 */
export function linkFromMessage(message) {
  const parsed = parseLinkMeta(messageText(message)) || {}
  const appUrl = String(message?.appUrl || '').trim()
  const appTitle = String(message?.appTitle || '').trim()
  const appDescription = String(message?.appDescription || '').trim()
  const url = parsed.url || (/^https?:\/\//i.test(appUrl) ? appUrl : '')
  const title = parsed.title || appTitle || ''
  const description = parsed.description || appDescription || ''
  return (url || title) ? { url, title, description } : null
}

/**
 * Group messages usually start with `wxid:\n` (or `wxid: text`). The candidate
 * must look like an account id, not an ordinary sentence prefix.
 */
export function parseGroupSenderPrefix(rawContent) {
  const text = normalizeXml(rawContent)
  const match = /^([A-Za-z0-9_@.-]{4,64})\s*[:：]\s*/.exec(text)
  if (!match) return ''
  const candidate = match[1]
  if (candidate.includes('://') || /^https?$/i.test(candidate)) return ''
  if (candidate.toLowerCase() === 'note') return ''
  return candidate
}

export function stripGroupPrefix(text, senderId) {
  const value = String(text ?? '')
  if (!senderId) return value
  const escaped = senderId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return value.replace(new RegExp(`^${escaped}\\s*[:：]\\s*`), '')
}

export function classifyMessage(message) {
  const rawType = Number(message?.localType) || 0
  const localType = normalizeLocalType(rawType)
  const highType = Math.floor(rawType / 0x1_0000_0000)
  const messageType = String(message?.messageType || '').toLowerCase()
  const text = messageText(message)
  const parsedText = String(message?.parsedContent || '')
  const file = parseFileAppmsg(text)
  const parsedFile = /^\[文件\]/.test(parsedText)
  const fileByEncodedType = highType === 6
  // Some media messages use encoded types (e.g. 21474836529 masks to 49).
  // Fall back to the XML shape when messageType is not useful.
  const looksLikeVideo = /<videomsg\b/i.test(text)
  const looksLikeImage = /<img\b/i.test(text) && !/<appmsg\b/i.test(text)
  // Mirrors weflow-cli's guard: a type-49 appmsg with title/url is a link
  // card, not a bare image hidden behind a cached thumbnail.
  const hasAppLinkMetadata = localType === 49 && Boolean(firstTag(text, 'title') || firstTag(text, 'url'))
  const encodedMedia = rawType > 0xffffffff && localType === 49 && highType !== 6

  if (message?.mediaKind === 'file' || file || parsedFile || fileByEncodedType) {
    let info = file
    if (!info && parsedFile) {
      const title = parsedText
        .replace(/^\[文件\]\s*/, '')
        .replace(/\s*\((?:[\d.]+\s*(?:B|KB|MB|GB))\)\s*$/i, '')
        .trim()
      info = { isFile: true, title: title || '未命名文件', md5: '', bytes: undefined }
    }
    if (!info) info = { isFile: true, title: '未命名文件', md5: '', bytes: undefined }
    return { type: 'file', file: info }
  }
  // Quote and merged-forward appmsgs also mask to localType 49. They must be
  // classified before the encoded-media/link heuristics below.
  if (messageType === 'quote'
      || (message?.quote && typeof message.quote === 'object' && message.quote.text)
      || (localType === 49 && /^\[引用\]/.test(parsedText))) {
    return { type: 'quote', file: null }
  }
  if (messageType === 'chat_history' || messageType === 'history'
      || (Array.isArray(message?.records) && message.records.length > 0)
      || /^\[(?:聊天记录|笔记)\]/.test(parsedText)) {
    return { type: 'history', file: null }
  }
  // Transfer/red-packet appmsgs also mask to localType 49; card/location have
  // their own base types. Classify them before the encoded-media heuristic.
  if (['transfer', 'redpacket', 'card', 'location'].includes(messageType)) {
    return { type: messageType, file: null }
  }
  if (messageType === 'miniprogram' || /^\[小程序\]/.test(parsedText)) {
    return { type: 'miniprogram', file: null }
  }
  if (messageType === 'video' || localType === 43 || looksLikeVideo) return { type: 'video', file: null }
  if (encodedMedia && !hasAppLinkMetadata) return { type: 'image', file: null }
  if (messageType === 'image' || localType === 3 || looksLikeImage) return { type: 'image', file: null }
  if (messageType === 'voice' || localType === 34) return { type: 'voice', file: null }
  if (messageType === 'emoji' || localType === 47) return { type: 'emoji', file: null }
  if (messageType === 'location' || localType === 48) return { type: 'location', file: null }
  if (messageType === 'link' || localType === 49) return { type: 'link', file: null }
  if (messageType === 'card' || localType === 42) return { type: 'card', file: null }
  if (messageType === 'call' || localType === 50) return { type: 'call', file: null }
  if (messageType === 'system' || localType === 10000) return { type: 'system', file: null }
  if (messageType === 'quote' || localType === 10002) return { type: 'quote', file: null }
  if (messageType === 'text' || localType === 1) return { type: 'text', file: null }
  return { type: 'other', file: null }
}

export const TYPE_LABELS = {
  text: '文字',
  image: '图片',
  file: '文件',
  video: '视频',
  voice: '语音',
  emoji: '表情',
  location: '位置',
  link: '链接',
  miniprogram: '小程序',
  history: '聊天记录',
  transfer: '转账',
  redpacket: '红包',
  card: '卡片',
  call: '通话',
  system: '系统',
  quote: '引用',
  other: '其他',
}

export function isMediaMessage(message) {
  const { type } = classifyMessage(message)
  return type === 'image' || type === 'file'
}

function usableText(value) {
  const text = String(value ?? '').trim()
  if (!text) return ''
  if (text.startsWith('<') && text.includes('>')) return ''
  return text
}

export function buildText(message, classified, senderId = '') {
  const { type, file } = classified
  const raw = messageText(message)
  const parsed = usableText(message?.parsedContent)
  const content = usableText(message?.content)

  switch (type) {
    case 'text':
      return stripGroupPrefix(parsed || content, senderId)
    case 'quote':
      return stripGroupPrefix(message.reply || parsed || content, senderId)
    case 'image':
      return parsed || '[图片]'
    case 'video': {
      const meta = parseVideoMeta(raw)
      return meta.playLength && Number(meta.playLength) > 0
        ? `[视频 ${meta.playLength}″]`
        : '[视频]'
    }
    case 'file':
      return `[文件] ${file?.title || '未命名文件'}`
    case 'voice':
      return parsed || '[语音]'
    case 'emoji':
      return parsed || '[表情]'
    case 'location':
      return parsed || '[位置]'
    case 'miniprogram': {
      const mp = parseMiniProgramMeta(raw)
      return parsed || `[小程序] ${mp?.title || '未命名小程序'}`
    }
    case 'link':
    case 'card':
    case 'history': {
      const link = parseLinkMeta(raw)
      return parsed || link?.title || (type === 'history' ? '[聊天记录]' : '[链接]')
    }
    case 'call':
      return parsed || '[语音通话]'
    case 'system':
      return parsed || content || '[系统消息]'
    default:
      return parsed || content || `[${TYPE_LABELS[type] || type}]`
  }
}

export function buildContactMap(contacts) {
  const map = new Map()
  for (const contact of contacts || []) {
    const id = String(contact?.username || '').trim()
    if (!id) continue
    const name = String(
      contact.remark || contact.displayName || contact.nickname || contact.alias || id,
    ).trim()
    map.set(id, name || id)
  }
  return map
}

export function senderIdOf(message) {
  const direct = String(message?.senderUsername || '').trim()
  // Some 3.x rows put the group id in senderUsername. The body prefix is the
  // only usable member identity there.
  if (direct && !direct.endsWith('@chatroom')) return direct
  return parseGroupSenderPrefix(messageText(message))
}

export function isSelfMessage(message, ownWxid = '') {
  const value = message?.isSend
  if (value === 1 || value === true || value === '1') return true
  if (value === 0 || value === false || value === '0') return false
  // Some rows have no isSend; fall back to the account id only when we know it.
  return Boolean(ownWxid && String(message?.senderUsername || '') === String(ownWxid))
}

export function resolveSender(message, options) {
  const { target, contactMap, meLabel = '我', kind, ownWxid = '' } = options
  if (isSelfMessage(message, ownWxid)) {
    return { senderId: String(message?.senderUsername || ''), sender: meLabel, isMe: true }
  }
  if (kind === 'group') {
    const senderId = senderIdOf(message)
    if (!senderId) return { senderId: '', sender: '未知成员', isMe: false }
    return { senderId, sender: contactMap?.get(senderId) || senderId, isMe: false }
  }
  if (kind === 'person') {
    const senderId = senderIdOf(message)
    const name = contactMap?.get(senderId) || target?.name || '对方'
    return { senderId, sender: name, isMe: false }
  }
  return { senderId: senderIdOf(message), sender: target?.name || '对方', isMe: false }
}

export function messageKey(message) {
  return `${message?.localId ?? ''}:${message?.createTime ?? ''}`
}

export { normalizeXml, firstTag, firstAttr }
