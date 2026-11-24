import { TYPE_LABELS } from './parse.mjs'
import { encodePath, humanBytes } from './media.mjs'
import { renderFaces } from './faces.mjs'

function mdEscapeText(text) {
  return String(text ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
}

function link(path) {
  return encodePath(path).replace(/\)/g, '%29')
}

export function renderMarkdown({ target, records, manifest }) {
  const lines = []
  const kind = target?.kind === 'group' ? '群聊' : '个人'
  const range = manifest?.query?.label || '全部时间'
  const stats = manifest?.stats || {}

  lines.push(`# 微信聊天记录：${target?.name || target?.id || '未命名会话'}`)
  lines.push('')
  lines.push(`- 类型：${kind}`)
  lines.push(`- 会话 ID：\`${target?.id || ''}\``)
  lines.push(`- 时间范围：${range}`)
  lines.push(`- 消息数：${stats.exportedMessages ?? records.length}`)
  lines.push(`- 导出时间：${manifest?.generatedAt || ''}`)
  lines.push('')
  lines.push('> 本地导出，未上传。媒体路径相对于本文件所在目录。')
  lines.push('')

  let currentDay = ''
  for (const record of records) {
    if (record.day !== currentDay) {
      currentDay = record.day
      lines.push('')
      lines.push(`## ${currentDay}`)
      lines.push('')
    }

    const time = /(\d{2}:\d{2}(?::\d{2})?)$/.exec(record.localTime)?.[1] || record.localTime
    const sender = record.isMe ? '我' : record.sender
    const revoked = record.revoked ? '（已撤回）' : ''
    lines.push(`**${time} · ${sender}${revoked}**  \n${mdEscapeText(renderFaces(record.text))}`)

    if (record.type === 'location' && record.location?.url) {
      const label = record.location.title || '位置'
      lines.push('')
      lines.push(`[📍 ${label}](${String(record.location.url).replace(/\)/g, '%29')})`)
    }
    if (record.type === 'card' && record.contact?.username) {
      lines.push('')
      lines.push(`名片账号：\`${record.contact.username}\``)
    }

    if (record.quote?.text) {
      const who = record.quote.sender ? ` ${record.quote.sender}` : ''
      lines.push('')
      lines.push(`> 引用${who}：${mdEscapeText(record.quote.text).replace(/\n/g, '\n> ')}`)
    }
    if (record.records?.length) {
      lines.push('')
      lines.push(`**💬 ${record.isNote ? '笔记' : '聊天记录'}（${record.records.length} 条）**`)
      for (const item of record.records) {
        const who = item.sender ? `${item.sender} ` : ''
        const when = item.time ? `${item.time} ` : ''
        const text = mdEscapeText(item.text || '').replace(/\n/g, '\n> ')
        lines.push(`> ${who}${when}${text}`)
        if (item.media) {
          if (item.kind === 'image') lines.push(`> ![转发图片](${link(item.media)})`)
          else if (item.kind === 'video') lines.push(`> [🎬 转发视频](${link(item.media)})`)
          else if (item.kind === 'file') lines.push(`> [📄 ${mdEscapeText(item.title || '转发文件')}](${link(item.media)})`)
        } else if (item.mediaStatus === 'missing') {
          const label = { image: '图片', video: '视频', file: '文件', voice: '语音' }[item.kind] || item.kind || '媒体'
          lines.push(`> [${label}未导出] 本机没有缓存`)
        }
      }
    }

    if (record.media?.image) {
      lines.push('')
      lines.push(`![图片](${link(record.media.image)})`)
    }
    if (record.media?.video) {
      const poster = record.media.poster ? `（封面：${record.media.poster}）` : ''
      lines.push('')
      lines.push(`[🎬 视频](${link(record.media.video)})${poster}`)
    }
    if (record.media?.file) {
      const size = record.fileSize ? `（${humanBytes(record.fileSize)}）` : ''
      lines.push('')
      lines.push(`[📄 ${record.fileName || '文件'}](${link(record.media.file)})${size}`)
    }
    if (record.media?.voice) {
      lines.push('')
      lines.push(`[🎤 语音](${link(record.media.voice)})`)
    }
    if (record.media?.sticker) {
      lines.push('')
      lines.push(`![表情](${link(record.media.sticker)})`)
    }
    if (record.link?.url) {
      lines.push('')
      lines.push(`[${record.link.title || record.link.url}](${record.link.url})`)
    }
    if (record.miniprogram) {
      const mp = record.miniprogram
      const meta = [mp.appName, mp.appId, mp.pagePath].filter(Boolean).join(' · ')
      lines.push('')
      lines.push(`🧩 **${mdEscapeText(mp.title || '小程序')}**${meta ? `（${mdEscapeText(meta)}）` : ''}`)
    }
    if (record.article?.path) {
      lines.push('')
      const href = String(record.article.path).replace(/\)/g, '%29')
      lines.push(`[📄 ${record.article.title || '公众号正文'}](${href})`)
    }
    if (record.missing?.length) {
      lines.push('')
      for (const item of record.missing) {
        const label = { image: '图片', file: '文件', video: '视频', voice: '语音', poster: '视频封面' }[item.kind] || item.kind
        lines.push(`> [${label}未导出] ${item.reason}`)
      }
    }
    lines.push('')
  }

  if (!records.length) {
    lines.push('_没有符合筛选条件的消息。_')
    lines.push('')
  }

  lines.push('---')
  lines.push('')
  const byType = stats.byType || {}
  const typeSummary = Object.entries(byType)
    .map(([type, count]) => `${TYPE_LABELS[type] || type} ${count}`)
    .join(' · ')
  lines.push(`统计：${typeSummary || '无'}`)
  lines.push('')
  return lines.join('\n')
}
