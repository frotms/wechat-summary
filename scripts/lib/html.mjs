import { TYPE_LABELS } from './parse.mjs'
import { encodePath, humanBytes } from './media.mjs'
import { renderFaces } from './faces.mjs'

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char])
}

function firstChar(text) {
  const value = String(text || '').trim()
  return value ? Array.from(value)[0] : '?'
}

function safeHttpUrl(url) {
  const value = String(url || '').trim()
  return /^https?:\/\//i.test(value) ? value : ''
}

function mediaUrl(path) {
  return escapeHtml(encodePath(path))
}

function shortTime(localTime) {
  const match = /(\d{2}:\d{2})(?::\d{2})?$/.exec(String(localTime || ''))
  return match ? match[1] : String(localTime || '')
}

function dayLabel(day) {
  const date = new Date(`${day}T00:00:00`)
  if (Number.isNaN(date.getTime())) return day
  const week = ['日', '一', '二', '三', '四', '五', '六'][date.getDay()]
  return `${day} 星期${week}`
}

function renderMedia(record) {
  const parts = []
  const media = record.media || {}

  if (media.image) {
    parts.push(`<a class="media-link" href="${mediaUrl(media.image)}" target="_blank" rel="noopener">`
      + `<img class="chat-image" loading="lazy" src="${mediaUrl(media.image)}" alt="图片"></a>`)
  }

  if (media.video) {
    const poster = media.poster ? ` poster="${mediaUrl(media.poster)}"` : ''
    parts.push(`<video class="chat-video" controls preload="metadata"${poster}>`
      + `<source src="${mediaUrl(media.video)}"></video>`
      + `<a class="video-download" href="${mediaUrl(media.video)}" target="_blank" rel="noopener">打开/下载视频</a>`)
  }

  if (media.file) {
    const size = record.fileSize ? `<span class="file-size">${escapeHtml(humanBytes(record.fileSize))}</span>` : ''
    parts.push(`<a class="file-card" href="${mediaUrl(media.file)}" download>`
      + `<span class="file-icon">📄</span><span class="file-name">${escapeHtml(record.fileName || '文件')}</span>${size}</a>`)
  }

  if (media.voice) {
    parts.push(`<audio class="chat-audio" controls preload="metadata" src="${mediaUrl(media.voice)}"></audio>`
      + `<a class="video-download" href="${mediaUrl(media.voice)}" target="_blank" rel="noopener">打开/下载语音</a>`)
  }

  if (media.sticker) {
    parts.push(`<a class="media-link" href="${mediaUrl(media.sticker)}" target="_blank" rel="noopener">`
      + `<img class="chat-sticker" loading="lazy" src="${mediaUrl(media.sticker)}" alt="表情"></a>`)
  }

  if (record.link?.url) {
    parts.push(`<a class="link-card" href="${escapeHtml(record.link.url)}" target="_blank" rel="noopener">`
      + `<span class="link-title">${escapeHtml(record.link.title || record.link.url)}</span>`
      + `<span class="link-url">${escapeHtml(record.link.url)}</span></a>`)
  }

  if (record.miniprogram) {
    const mp = record.miniprogram
    const meta = [mp.appName, mp.appId, mp.pagePath].filter(Boolean).join(' · ')
    const title = escapeHtml(mp.title || '小程序')
    const body = `<span class="link-title">🧩 ${title}</span>`
      + (meta ? `<span class="link-url">${escapeHtml(meta)}</span>` : '')
    if (/^https?:\/\//i.test(mp.url || '')) {
      parts.push(`<a class="link-card" href="${escapeHtml(mp.url)}" target="_blank" rel="noopener">${body}</a>`)
    } else {
      parts.push(`<div class="link-card">${body}</div>`)
    }
  }

  if (record.article?.path) {
    parts.push(`<a class="article-card" href="${mediaUrl(record.article.path)}" target="_blank" rel="noopener">`
      + `<span class="article-icon">📄</span>`
      + `<span class="article-title">${escapeHtml(record.article.title || '公众号正文')}</span>`
      + `<span class="article-url">${escapeHtml(record.article.url || '')}</span></a>`)
  }

  return parts.join('')
}

function renderMissing(record) {
  if (!record.missing?.length) return ''
  const labels = {
    image: '图片',
    file: '文件',
    video: '视频',
    voice: '语音',
    poster: '视频封面',
    emoji: '表情',
  }
  const reasons = {
    'image-not-local': '本机没有缓存原图（可能从未下载或已被微信清理）',
    'file-not-local': '本机没有文件本体（可能从未下载或已被微信清理）',
    'file-not-on-disk': '清单里有记录，但文件已不在磁盘上',
    'video-not-local': '本机 msg/video 目录里没有这个视频',
    'voice-not-local': '本机没有这条语音的 SILK/WAV 数据',
    'sticker-not-local': '本机没有缓存这个表情包（可能从未下载或已被微信清理）',
    'sticker-map-disabled': '本次导出没有生成表情包映射（--no-sticker-map）',
    'video-root-not-found': '没有找到微信视频目录，无法定位视频',
    'video-encrypted-v2': '视频文件是微信 V2 加密格式，当前只落盘未解密',
    'unrecognized-video-magic': '视频文件头无法识别，可能不是标准 MP4',
    'media-extraction-failed': '媒体抽取失败（检查 Python 依赖）',
    'media-skipped': '本次导出没有抽取媒体（--no-media 或 --media-strategy none）',
    'forward-media-not-local': '合并转发内层媒体本机没有缓存；未从 CDN 猜测下载',
  }
  return record.missing.map((item) => {
    const label = labels[item.kind] || item.kind
    const reason = reasons[item.reason] || item.reason
    return `<div class="missing">[${escapeHtml(label)}未导出] ${escapeHtml(reason)}</div>`
  }).join('')
}

function renderQuote(record) {
  const quote = record.quote
  if (!quote || !quote.text) return ''
  const sender = quote.sender ? `引用 ${escapeHtml(quote.sender)}` : '引用'
  return `<blockquote class="quote"><div class="quote-meta">${sender}</div>`
    + `<div class="quote-text">${escapeHtml(renderFaces(quote.text))}</div></blockquote>`
}

function renderRecords(record) {
  if (!Array.isArray(record.records) || !record.records.length) return ''
  const rows = record.records.map((item) => {
    const who = item.sender ? `<span class="rec-sender">${escapeHtml(item.sender)}</span>` : ''
    const time = item.time ? `<span class="rec-time">${escapeHtml(item.time)}</span>` : ''
    const text = `<span class="rec-text">${escapeHtml(item.text || '')}</span>`
    let media = ''
    if (item.media) {
      if (item.kind === 'image') {
        media = `<a class="media-link" href="${mediaUrl(item.media)}" target="_blank" rel="noopener">`
          + `<img class="forward-image" loading="lazy" src="${mediaUrl(item.media)}" alt="转发图片"></a>`
      } else if (item.kind === 'video') {
        media = `<a class="video-download" href="${mediaUrl(item.media)}" target="_blank" rel="noopener">🎬 打开/下载转发视频</a>`
      } else if (item.kind === 'file') {
        media = `<a class="file-card" href="${mediaUrl(item.media)}" download>📄 ${escapeHtml(item.title || '转发文件')}</a>`
      }
    } else if (item.mediaStatus === 'missing') {
      const label = { image: '图片', video: '视频', file: '文件', voice: '语音' }[item.kind] || item.kind || '媒体'
      media = `<span class="missing">[${escapeHtml(label)}未导出] 本机没有缓存</span>`
    }
    return `<div class="rec-item">${who}${time}${text}${media}</div>`
  }).join('')
  const title = record.isNote ? '笔记' : '聊天记录'
  return `<details class="record"><summary>💬 ${title} · ${record.records.length} 条</summary>`
    + `<div class="record-list">${rows}</div></details>`
}

function renderSpecial(record) {
  if (record.type === 'location') {
    const loc = record.location || {}
    const label = loc.title || record.text || '位置'
    const coords = [loc.lat, loc.lng].filter(Boolean).join(', ')
    const meta = [loc.city, coords].filter(Boolean).join(' · ')
    const inner = `<span class="special-icon">📍</span><span class="special-title">${escapeHtml(label)}</span>`
      + (meta ? `<span class="special-meta">${escapeHtml(meta)}</span>` : '')
    const url = safeHttpUrl(loc.url)
    return url
      ? `<a class="special location" href="${escapeHtml(url)}" target="_blank" rel="noopener">${inner}</a>`
      : `<div class="special location">${inner}</div>`
  }
  if (record.type === 'transfer') {
    return `<div class="special transfer"><span class="special-icon">💸</span>`
      + `<span class="special-title">转账</span>`
      + `<span class="special-meta">${escapeHtml(record.amount || record.text || '')}</span></div>`
  }
  if (record.type === 'redpacket') {
    const redpacket = record.redpacket || {}
    return `<div class="special redpacket"><span class="special-icon">🧧</span>`
      + `<span class="special-title">${escapeHtml(redpacket.title || record.text || '微信红包')}</span>`
      + (redpacket.description ? `<span class="special-meta">${escapeHtml(redpacket.description)}</span>` : '')
      + '</div>'
  }
  if (record.type === 'card') {
    const contact = record.contact || {}
    return `<div class="special card"><span class="special-icon">👤</span>`
      + `<span class="special-title">${escapeHtml(contact.title || record.text || '名片')}</span>`
      + (contact.username ? `<span class="special-meta">${escapeHtml(contact.username)}</span>` : '')
      + '</div>'
  }
  return ''
}

function renderMessage(record) {
  if (record.type === 'system') {
    return `<div class="msg system" data-type="system" data-sender="${escapeHtml(record.sender)}" data-text="${escapeHtml(record.text.toLowerCase())}">`
      + `<span>${escapeHtml(record.text)}</span><time>${escapeHtml(shortTime(record.localTime))}</time></div>`
  }

  const side = record.isMe ? 'me' : 'them'
  const senderName = record.isMe ? '我' : record.sender
  const searchText = [
    record.text,
    record.fileName,
    record.link?.title,
    record.link?.url,
    record.article?.title,
    record.quote?.text,
    ...(record.records || []).map((item) => item.text),
    record.amount,
    record.location?.title,
    record.location?.city,
    record.contact?.username,
    record.redpacket?.title,
    record.revoked ? '已撤回' : '',
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  const media = renderMedia(record)
  const missing = renderMissing(record)
  const special = renderSpecial(record)
  const hasOwnMedia = Boolean(record.media?.image || record.media?.video || record.media?.file || record.media?.voice || record.media?.sticker)
  const showText = record.text
    && !special
    && !(record.type === 'image' && record.text === '[图片]')
    && !(record.type === 'emoji' && record.text === '[表情]')
    && !(hasOwnMedia && (record.type === 'file' || record.type === 'video' || record.type === 'voice'))

  return `<article class="msg ${side} type-${escapeHtml(record.type)}" data-type="${escapeHtml(record.type)}" `
    + `data-sender="${escapeHtml(record.sender)}" data-text="${escapeHtml(searchText)}" data-seq="${record.seq}">`
    + `<div class="avatar ${side}">${escapeHtml(record.isMe ? '我' : firstChar(record.sender))}</div>`
    + `<div class="bubble-wrap">`
    + `<div class="meta"><span class="sender">${escapeHtml(senderName)}</span>`
    + `${record.revoked ? '<span class="revoked-badge">已撤回</span>' : ''}`
    + `<time>${escapeHtml(shortTime(record.localTime))}</time></div>`
    + `<div class="bubble">${showText ? `<div class="text">${escapeHtml(renderFaces(record.text))}</div>` : ''}${special}${renderQuote(record)}${renderRecords(record)}${media}${missing}</div>`
    + `</div></article>`
}

function typeChips(records) {
  const counts = {}
  for (const record of records) counts[record.type] = (counts[record.type] || 0) + 1
  const order = ['text', 'image', 'file', 'video', 'voice', 'emoji', 'link', 'miniprogram', 'history', 'transfer', 'redpacket', 'card', 'location', 'system', 'quote', 'other']
  const present = order.filter((type) => counts[type])
  const chips = [`<button class="chip active" data-filter-type="all">全部 <b>${records.length}</b></button>`]
  for (const type of present) {
    chips.push(`<button class="chip" data-filter-type="${escapeHtml(type)}">${escapeHtml(TYPE_LABELS[type] || type)} <b>${counts[type]}</b></button>`)
  }
  return { chips: chips.join(''), counts }
}

function senderOptions(records) {
  const counts = new Map()
  for (const record of records) {
    counts.set(record.sender, (counts.get(record.sender) || 0) + 1)
  }
  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 120)
  return rows.map(([name, count]) => `<option value="${escapeHtml(name)}">${escapeHtml(name)} (${count})</option>`).join('')
}

export function renderHtml({ target, records, manifest, htmlMaxMessages = 50_000 }) {
  if (records.length > htmlMaxMessages) {
    return {
      skipped: true,
      reason: `消息数 ${records.length} 超过 --max-html-messages ${htmlMaxMessages}，未生成 HTML`,
    }
  }

  const { chips, counts } = typeChips(records)
  const media = manifest?.stats?.media || {}
  const totalFound = (media.images?.found || 0) + (media.files?.found || 0) + (media.videos?.found || 0) + (media.voices?.found || 0) + (media.emojis?.found || 0)
  const totalMissing = (media.images?.missing || 0) + (media.files?.missing || 0) + (media.videos?.missing || 0) + (media.voices?.missing || 0) + (media.emojis?.missing || 0)

  const dayCounts = new Map()
  for (const record of records) dayCounts.set(record.day, (dayCounts.get(record.day) || 0) + 1)

  let body = ''
  let currentDay = ''
  for (const record of records) {
    if (record.day !== currentDay) {
      if (currentDay) body += '</section>'
      currentDay = record.day
      body += `<section class="day" data-day="${escapeHtml(currentDay)}">`
        + `<h2 class="day-title">${escapeHtml(dayLabel(currentDay))}`
        + `<span class="day-count">${dayCounts.get(currentDay) || 0} 条</span></h2>`
    }
    body += renderMessage(record)
  }
  if (currentDay) body += '</section>'

  const kindLabel = target?.kind === 'group' ? '群聊' : '个人'
  const generated = escapeHtml(manifest?.generatedAt || new Date().toISOString())
  const range = escapeHtml(manifest?.query?.label || '全部时间')
  const title = escapeHtml(target?.name || target?.id || '微信聊天记录')

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title} · 微信聊天记录</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #f4f5f7;
  --panel: #ffffff;
  --text: #1f2328;
  --muted: #667085;
  --line: #e4e7ec;
  --brand: #07c160;
  --brand-soft: #e8f8ef;
  --me: #d9fdd3;
  --me-text: #14231a;
  --them: #ffffff;
  --warn: #b54708;
  --warn-bg: #fff7ed;
  --shadow: 0 1px 2px rgba(16, 24, 40, .06), 0 1px 3px rgba(16, 24, 40, .10);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111418;
    --panel: #1a1f24;
    --text: #e6e8eb;
    --muted: #98a2b3;
    --line: #2a3037;
    --brand: #2bd47d;
    --brand-soft: #163326;
    --me: #1f4d33;
    --me-text: #e8fff1;
    --them: #1a1f24;
    --warn: #fdb022;
    --warn-bg: #2a2113;
    --shadow: 0 1px 2px rgba(0,0,0,.3);
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
header.top {
  position: sticky; top: 0; z-index: 20; background: color-mix(in srgb, var(--panel) 94%, transparent);
  border-bottom: 1px solid var(--line); backdrop-filter: blur(10px);
}
.wrap { max-width: 980px; margin: 0 auto; padding: 0 16px; }
.title-row { display: flex; align-items: center; gap: 10px; padding: 14px 0 8px; flex-wrap: wrap; }
.title-row h1 { font-size: 18px; margin: 0; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: var(--brand-soft); color: var(--brand); font-weight: 600; }
.muted { color: var(--muted); font-size: 12px; }
.stats { display: flex; gap: 8px; flex-wrap: wrap; padding: 6px 0 10px; }
.stat { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 6px 10px; font-size: 12px; box-shadow: var(--shadow); }
.stat b { font-size: 14px; }
.toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 0 12px; }
.toolbar input[type=search] {
  flex: 1 1 220px; min-width: 180px; padding: 8px 10px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--panel); color: var(--text); outline: none;
}
.toolbar input[type=search]:focus { border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.toolbar select { padding: 8px 10px; border-radius: 10px; border: 1px solid var(--line); background: var(--panel); color: var(--text); }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { border: 1px solid var(--line); background: var(--panel); color: var(--muted); border-radius: 999px; padding: 5px 10px; cursor: pointer; font-size: 12px; }
.chip b { color: var(--text); margin-left: 2px; }
.chip.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.chip.active b { color: #fff; }
main { max-width: 980px; margin: 0 auto; padding: 16px; }
.day { margin-bottom: 22px; }
.day-title {
  display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--muted);
  font-weight: 600; margin: 18px 0 10px; position: sticky; top: 118px; z-index: 10;
  background: color-mix(in srgb, var(--bg) 92%, transparent); padding: 6px 0; backdrop-filter: blur(6px);
}
.day-count { font-weight: 400; }
.msg { display: flex; gap: 10px; margin: 10px 0; align-items: flex-start; }
.msg[hidden] { display: none; }
.msg.me { flex-direction: row-reverse; }
.avatar { width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center; font-size: 13px; font-weight: 700; flex: 0 0 auto; }
.avatar.them { background: var(--brand-soft); color: var(--brand); }
.avatar.me { background: var(--brand); color: #fff; }
.bubble-wrap { min-width: 0; max-width: min(76%, 640px); }
.msg.me .bubble-wrap { display: flex; flex-direction: column; align-items: flex-end; }
.meta { display: flex; gap: 8px; align-items: baseline; color: var(--muted); font-size: 11px; margin: 0 2px 3px; }
.meta .sender { color: var(--muted); font-weight: 600; }
.revoked-badge { color: #b45309; border: 1px solid #f59e0b; border-radius: 999px; padding: 0 6px; font-size: 10px; }
.bubble {
  background: var(--them); border: 1px solid var(--line); border-radius: 14px;
  padding: 8px 11px; box-shadow: var(--shadow); overflow-wrap: anywhere;
}
.msg.me .bubble { background: var(--me); border-color: transparent; color: var(--me-text); border-top-right-radius: 4px; }
.msg.them .bubble { border-top-left-radius: 4px; }
.text { white-space: pre-wrap; word-break: break-word; }
.chat-image { display: block; max-width: 100%; max-height: 420px; border-radius: 10px; margin-top: 6px; cursor: zoom-in; }
.chat-sticker { display: block; max-width: 240px; max-height: 240px; border-radius: 10px; margin-top: 6px; }
.chat-video { display: block; max-width: 100%; max-height: 420px; border-radius: 10px; margin-top: 6px; background: #000; }
.chat-audio { display: block; width: 100%; max-width: 320px; margin-top: 6px; }
.video-download { display: inline-block; margin-top: 4px; color: var(--muted); font-size: 12px; }
.file-card {
  display: flex; align-items: center; gap: 8px; margin-top: 6px; padding: 9px 11px;
  border: 1px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--text); text-decoration: none;
}
.file-card:hover { border-color: var(--brand); }
.file-icon { font-size: 20px; }
.file-name { font-weight: 600; overflow-wrap: anywhere; }
.file-size { color: var(--muted); font-size: 12px; margin-left: auto; white-space: nowrap; }
.link-card {
  display: flex; flex-direction: column; gap: 2px; margin-top: 6px; padding: 9px 11px;
  border: 1px solid var(--line); border-left: 3px solid var(--brand); border-radius: 8px;
  background: var(--panel); color: var(--text); text-decoration: none;
}
.link-title { font-weight: 600; }
.link-url { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.article-card {
  display: flex; flex-direction: column; gap: 2px; margin-top: 6px; padding: 9px 11px;
  border: 1px solid var(--line); border-left: 3px solid var(--brand); border-radius: 8px;
  background: var(--panel); color: var(--text); text-decoration: none;
}
.article-card:hover { border-color: var(--brand); }
.article-icon { font-size: 16px; }
.article-title { font-weight: 600; }
.article-url { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.quote {
  margin-top: 6px; padding: 6px 9px; border-left: 3px solid var(--line); border-radius: 6px;
  background: color-mix(in srgb, var(--panel) 72%, transparent); font-size: 12px; color: var(--muted);
}
.quote-meta { font-weight: 600; margin-bottom: 2px; }
.quote-text { white-space: pre-wrap; overflow-wrap: anywhere; }
.record { margin-top: 6px; }
.record summary { cursor: pointer; font-size: 12px; color: var(--brand); font-weight: 600; }
.record-list { margin-top: 6px; border-left: 2px solid var(--line); padding-left: 8px; }
.rec-item { padding: 4px 0; border-bottom: 1px dashed var(--line); font-size: 12px; }
.rec-item:last-child { border-bottom: 0; }
.rec-sender { font-weight: 600; margin-right: 6px; }
.rec-time { color: var(--muted); margin-right: 6px; }
.rec-text { display: block; margin-top: 2px; white-space: pre-wrap; overflow-wrap: anywhere; }
.special {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px;
  margin-top: 2px; padding: 9px 11px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel); color: var(--text); text-decoration: none;
}
.special.location:hover { border-color: var(--brand); }
.special-icon { font-size: 18px; }
.special-title { font-weight: 600; }
.special-meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.missing { margin-top: 6px; padding: 6px 9px; border-radius: 8px; background: var(--warn-bg); color: var(--warn); font-size: 12px; }
.msg.system { justify-content: center; color: var(--muted); font-size: 12px; }
.msg.system span { background: var(--panel); border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; }
.msg.system time { margin-left: 6px; }
.empty { text-align: center; color: var(--muted); padding: 40px 0; }
footer { max-width: 980px; margin: 0 auto; padding: 24px 16px 48px; color: var(--muted); font-size: 12px; }
@media (max-width: 640px) {
  .bubble-wrap { max-width: 86%; }
  .day-title { top: 132px; }
  .avatar { width: 28px; height: 28px; font-size: 12px; }
}
</style>
</head>
<body>
<header class="top"><div class="wrap">
  <div class="title-row">
    <h1>${title}</h1>
    <span class="badge">${kindLabel}</span>
    <span class="muted">${range}</span>
  </div>
  <div class="stats">
    <span class="stat">消息 <b>${records.length}</b></span>
    <span class="stat">媒体已导出 <b>${totalFound}</b></span>
    <span class="stat">媒体缺失 <b>${totalMissing}</b></span>
    <span class="stat">语音 <b>${media.voices?.found || 0}</b></span>
    <span class="stat">生成于 <b>${generated}</b></span>
  </div>
  <div class="toolbar">
    <input id="q" type="search" placeholder="搜索文字、文件名、链接…" autocomplete="off">
    <select id="sender"><option value="all">全部发送者</option>${senderOptions(records)}</select>
    <div class="chips" id="chips">${chips}</div>
    <span class="muted" id="visible-count"></span>
  </div>
</div></header>
<main id="messages">${body || '<div class="empty">没有符合筛选条件的消息</div>'}</main>
<footer>
  本地导出，未上传任何内容。媒体路径相对于本目录，移动整个目录后仍可打开。<br>
  缺失媒体表示源数据不在本机或抽取失败，不是路径写错。
</footer>
<script>
(function () {
  var state = { type: 'all', sender: 'all', q: '' };
  var messages = Array.prototype.slice.call(document.querySelectorAll('.msg'));
  var days = Array.prototype.slice.call(document.querySelectorAll('.day'));
  var visible = document.getElementById('visible-count');

  function apply() {
    var shown = 0;
    messages.forEach(function (el) {
      var okType = state.type === 'all' || el.dataset.type === state.type;
      var okSender = state.sender === 'all' || el.dataset.sender === state.sender;
      var okQ = !state.q || (el.dataset.text || '').indexOf(state.q) !== -1;
      var ok = okType && okSender && okQ;
      el.hidden = !ok;
      if (ok) shown += 1;
    });
    days.forEach(function (day) {
      var any = Array.prototype.some.call(day.querySelectorAll('.msg'), function (el) { return !el.hidden; });
      day.hidden = !any;
    });
    if (visible) visible.textContent = '显示 ' + shown + ' / ' + messages.length;
  }

  document.getElementById('q').addEventListener('input', function (event) {
    state.q = event.target.value.trim().toLowerCase();
    apply();
  });
  document.getElementById('sender').addEventListener('change', function (event) {
    state.sender = event.target.value;
    apply();
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-filter-type]'), function (button) {
    button.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('[data-filter-type]'), function (other) {
        other.classList.remove('active');
      });
      button.classList.add('active');
      state.type = button.dataset.filterType;
      apply();
    });
  });
  apply();
})();
</script>
</body>
</html>
`
}

export function renderBatchIndex({ generatedAt, query, bundles }) {
  const cards = bundles.map((bundle) => {
    const stats = bundle.stats || {}
    const target = bundle.html
      ? `${bundle.dir}/index.html`
      : bundle.markdown
        ? `${bundle.dir}/transcript.md`
        : `${bundle.dir}/manifest.json`
    const href = encodePath(target)
    return `<a class="card" href="${escapeHtml(href)}">`
      + `<div class="card-title">${escapeHtml(bundle.target.name || bundle.target.id)}</div>`
      + `<div class="card-meta">${bundle.target.kind === 'group' ? '群聊' : '个人'} · `
      + `${stats.exportedMessages ?? 0} 条 · 图片 ${stats.media?.images?.found ?? 0} · `
      + `文件 ${stats.media?.files?.found ?? 0} · 视频 ${stats.media?.videos?.found ?? 0}</div>`
      + `<div class="card-id">${escapeHtml(bundle.target.id)}</div></a>`
  }).join('')

  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>微信聊天记录导出</title>
<style>
:root{color-scheme:light dark;--bg:#f4f5f7;--panel:#fff;--text:#1f2328;--muted:#667085;--line:#e4e7ec;--brand:#07c160}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--panel:#1a1f24;--text:#e6e8eb;--muted:#98a2b3;--line:#2a3037;--brand:#2bd47d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:900px;margin:0 auto;padding:28px 16px 56px}h1{font-size:22px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.card{display:block;padding:14px 16px;border:1px solid var(--line);border-radius:14px;background:var(--panel);color:var(--text);text-decoration:none}
.card:hover{border-color:var(--brand)}.card-title{font-weight:700;margin-bottom:4px}.card-meta{color:var(--muted);font-size:12px}
.card-id{color:var(--muted);font-size:11px;margin-top:8px;overflow-wrap:anywhere}
.note{margin-top:24px;color:var(--muted);font-size:12px}
</style></head><body><main>
<h1>微信聊天记录导出</h1>
<div class="sub">${escapeHtml(query?.label || '全部时间')} · 生成于 ${escapeHtml(generatedAt)} · ${bundles.length} 个会话</div>
<div class="grid">${cards || '<div class="note">没有导出任何会话。</div>'}</div>
<div class="note">全部内容只在本机。交付给他人前请确认隐私边界；缺失媒体表示源数据不在本机。</div>
</main></body></html>
`
}
