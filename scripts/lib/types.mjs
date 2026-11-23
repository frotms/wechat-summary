// Message-type helpers shared by the export wrappers.

// Tokens that require media extraction/mapping. Everything else (text, quote,
// history, link, location, card, call, system, other) can be exported without
// touching the media pipeline.
export const MEDIA_TYPE_TOKENS = new Set([
  'all', '*', '全部',
  'image', 'images', 'img', '图片', '照片',
  'file', 'files', '文件', '文档',
  'video', 'videos', '视频',
  'voice', 'audio', '语音',
  'emoji', '表情', 'sticker',
])

// Types that make wechat-export call the native `export --media` path
// (mediaRequested = image || file). In that path native-backend already
// writes images/files/videos/posters/voice/stickers, so a separate
// wechat42-media-map is redundant.
export const NATIVE_ASSET_TOKENS = new Set([
  'all', '*', '全部',
  'image', 'images', 'img', '图片', '照片',
  'file', 'files', '文件', '文档',
])

function typeTokens(value) {
  if (value === undefined || value === null || value === true) return null
  const tokens = String(value)
    .split(/[\s,，、]+/)
    .map((token) => token.trim().toLowerCase())
    .filter(Boolean)
  return tokens.length ? tokens : null
}

/**
 * Whether a `--types` value needs image/file/video/voice/sticker handling.
 * Unknown/empty values are treated as media because the default type set is
 * media-bearing; callers should fail safe rather than skip needed work.
 */
export function typesNeedMedia(value) {
  const tokens = typeTokens(value)
  if (!tokens) return true
  return tokens.some((token) => MEDIA_TYPE_TOKENS.has(token))
}

/**
 * Whether the native `export --media` path will run. In that case native
 * already produces the media assets, so wechat42-media-map can be skipped.
 */
export function typesNeedNativeAssets(value) {
  const tokens = typeTokens(value)
  if (!tokens) return true
  return tokens.some((token) => NATIVE_ASSET_TOKENS.has(token))
}
