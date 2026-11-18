import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export const VIDEO_MD5 = 'abcdef0123456789abcdef0123456789'

function ts(year, month, day, hour, minute = 0) {
  return Math.floor(new Date(year, month - 1, day, hour, minute, 0).getTime() / 1000)
}

export function jpegBytes() {
  return Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0xff, 0xd9])
}

export function mp4Bytes() {
  return Buffer.from([
    0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70,
    0x6d, 0x70, 0x34, 0x32, 0x00, 0x00, 0x00, 0x00,
  ])
}

export function wavBytes() {
  const data = Buffer.alloc(200, 0)
  const header = Buffer.alloc(44)
  header.write('RIFF', 0, 'latin1')
  header.writeUInt32LE(36 + data.length, 4)
  header.write('WAVE', 8, 'latin1')
  header.write('fmt ', 12, 'latin1')
  header.writeUInt32LE(16, 16)
  header.writeUInt16LE(1, 20)
  header.writeUInt16LE(1, 22)
  header.writeUInt32LE(24000, 24)
  header.writeUInt32LE(48000, 28)
  header.writeUInt16LE(2, 32)
  header.writeUInt16LE(16, 34)
  header.write('data', 36, 'latin1')
  header.writeUInt32LE(data.length, 40)
  return Buffer.concat([header, data])
}

export function pngBytes() {
  return Buffer.from([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
    0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  ])
}

export function makeWorkspace() {
  const root = mkdtempSync(join(tmpdir(), 'wechat-export-test-'))
  const accountRoot = join(root, 'xwechat_files', 'wxid_test_1234')
  const videoRoot = join(accountRoot, 'msg', 'video')
  const monthDir = join(videoRoot, '2026-01')
  mkdirSync(monthDir, { recursive: true })
  writeFileSync(join(monthDir, `${VIDEO_MD5}.mp4`), mp4Bytes())
  writeFileSync(join(monthDir, `${VIDEO_MD5}.jpg`), jpegBytes())

  const messageDb = join(accountRoot, 'db_storage', 'message', 'message_0.db')
  mkdirSync(join(accountRoot, 'db_storage', 'message'), { recursive: true })
  writeFileSync(messageDb, '')

  const configPath = join(root, 'weflow-config.json')
  writeFileSync(configPath, JSON.stringify({ ntDbPath: messageDb, wxid: 'wxid_me' }, null, 2), 'utf8')

  const group = '123@chatroom'
  const person = 'wxid_zhangsan'
  const official = 'gh_news'

  const messages = {
    [group]: [
      {
        localId: 1, serverId: 's1', localType: 1, createTime: ts(2026, 1, 5, 10, 0),
        isSend: 0, senderUsername: 'wxid_lisi', content: '大家好', rawContent: '大家好',
        parsedContent: '大家好', messageType: 'text',
      },
      {
        localId: 2, serverId: 's2', localType: 3, createTime: ts(2026, 1, 5, 10, 1),
        isSend: 0, senderUsername: 'wxid_lisi',
        content: '<msg><img md5="11111111111111111111111111111111"/></msg>',
        rawContent: '<msg><img md5="11111111111111111111111111111111"/></msg>',
        parsedContent: '[图片]', messageType: 'image',
      },
      {
        localId: 3, serverId: 's3', localType: 49, createTime: ts(2026, 1, 5, 10, 2),
        isSend: 0, senderUsername: 'wxid_lisi',
        content: '<msg><appmsg><title>报告.pdf</title><type>6</type><md5>2222</md5><appattach><totallen>1234</totallen></appattach></appmsg></msg>',
        rawContent: '<msg><appmsg><title>报告.pdf</title><type>6</type><md5>2222</md5><appattach><totallen>1234</totallen></appattach></appmsg></msg>',
        parsedContent: '报告.pdf', messageType: 'link',
      },
      {
        localId: 4, serverId: 's4', localType: 43, createTime: ts(2026, 1, 5, 10, 3),
        isSend: 0, senderUsername: 'wxid_lisi',
        content: `<msg><videomsg md5="${VIDEO_MD5}" playlength="12"/></msg>`,
        rawContent: `<msg><videomsg md5="${VIDEO_MD5}" playlength="12"/></msg>`,
        parsedContent: '[视频]', messageType: 'video',
      },
      {
        localId: 5, serverId: 's5', localType: 1, createTime: ts(2026, 1, 6, 9, 0),
        isSend: 1, senderUsername: 'wxid_me', content: '收到', rawContent: '收到',
        parsedContent: '收到', messageType: 'text',
      },
      {
        localId: 6, serverId: 's6', localType: 34, createTime: ts(2026, 1, 6, 9, 1),
        isSend: 0, senderUsername: 'wxid_lisi',
        content: '<msg><voicelength>3000</voicelength></msg>',
        rawContent: '<msg><voicelength>3000</voicelength></msg>',
        parsedContent: '[语音 3s]', messageType: 'voice',
      },
      {
        localId: 7, serverId: 's7', localType: 47, createTime: ts(2026, 1, 6, 9, 2),
        isSend: 0, senderUsername: 'wxid_lisi',
        content: '<msg><emoji md5="abcdef0123456789abcdef0123456789"/></msg>',
        rawContent: '<msg><emoji md5="abcdef0123456789abcdef0123456789"/></msg>',
        parsedContent: '[表情]', messageType: 'emoji',
      },
    ],
    [person]: [
      {
        localId: 10, serverId: 'p1', localType: 1, createTime: ts(2026, 1, 5, 11, 0),
        isSend: 0, senderUsername: person, content: '你好', rawContent: '你好',
        parsedContent: '你好', messageType: 'text',
      },
      {
        localId: 11, serverId: 'p2', localType: 3, createTime: ts(2026, 1, 5, 11, 1),
        isSend: 0, senderUsername: person,
        content: '<msg><img md5="33333333333333333333333333333333"/></msg>',
        rawContent: '<msg><img md5="33333333333333333333333333333333"/></msg>',
        parsedContent: '[图片]', messageType: 'image',
      },
    ],
    [official]: [
      {
        localId: 20, serverId: 'o1', localType: 1, createTime: ts(2026, 1, 5, 12, 0),
        isSend: 0, senderUsername: official, content: '推送', rawContent: '推送',
        parsedContent: '推送', messageType: 'text',
      },
    ],
  }

  const fixture = {
    sessions: [
      { username: group, displayName: '项目群', lastTimestamp: ts(2026, 1, 6, 9, 0), unreadCount: 2, summary: '收到' },
      { username: person, displayName: '张三', lastTimestamp: ts(2026, 1, 5, 11, 1), unreadCount: 0, summary: '你好' },
      { username: official, displayName: '某公众号', lastTimestamp: ts(2026, 1, 5, 12, 0), unreadCount: 0, summary: '推送' },
    ],
    contacts: [
      { username: group, displayName: '项目群' },
      { username: person, remark: '张三', nickname: 'ZS' },
      { username: 'wxid_lisi', remark: '李四', nickname: 'LS' },
      { username: 'wxid_me', remark: '我' },
      { username: official, displayName: '某公众号' },
    ],
    messages,
  }

  const fixturePath = join(root, 'fixture.json')
  writeFileSync(fixturePath, JSON.stringify(fixture, null, 2), 'utf8')

  const voicePath = join(root, 'voice.wav')
  writeFileSync(voicePath, wavBytes())
  const voiceMapPath = join(root, 'voice-map.json')
  writeFileSync(voiceMapPath, JSON.stringify({
    schema: 'wechat42-media-map/v1',
    items: {
      [`6:${ts(2026, 1, 6, 9, 1)}`]: {
        kind: 'voice',
        time: '2026-01-06 09:01:00',
        sender: '李四',
        media: voicePath,
      },
    },
  }, null, 2), 'utf8')

  const stickerPath = join(root, 'sticker.png')
  writeFileSync(stickerPath, pngBytes())
  const stickerMapPath = join(root, 'sticker-map.json')
  writeFileSync(stickerMapPath, JSON.stringify({
    schema: 'wechat42-media-map/v1',
    items: {
      [`7:${ts(2026, 1, 6, 9, 2)}`]: {
        kind: 'sticker',
        time: '2026-01-06 09:02:00',
        sender: '李四',
        media: stickerPath,
        sticker_md5: 'abcdef0123456789abcdef0123456789',
      },
    },
  }, null, 2), 'utf8')

  // The 4.1/4.2 wrappers merge the sticker map into the media map, so the
  // end-to-end path sees one file with both kinds of entries.
  const combinedMapPath = join(root, 'media-map.json')
  writeFileSync(combinedMapPath, JSON.stringify({
    schema: 'wechat42-media-map-merged/v1',
    items: {
      [`6:${ts(2026, 1, 6, 9, 1)}`]: {
        kind: 'voice',
        time: '2026-01-06 09:01:00',
        sender: '李四',
        media: voicePath,
      },
      [`7:${ts(2026, 1, 6, 9, 2)}`]: {
        kind: 'sticker',
        time: '2026-01-06 09:02:00',
        sender: '李四',
        media: stickerPath,
        sticker_md5: 'abcdef0123456789abcdef0123456789',
      },
    },
  }, null, 2), 'utf8')

  const revokeMapPath = join(root, 'revoke-map.json')
  writeFileSync(revokeMapPath, JSON.stringify({
    schema: 'wechat-revoke-map-merged/v1',
    items: {
      [`1:${ts(2026, 1, 5, 10, 0)}`]: {
        revoked: true,
        kind: 'batch',
        batch_id: 123456,
        msg_unique_id: `${group}_${ts(2026, 1, 5, 10, 0)}_1`,
        revoke_time: null,
        content: '',
        server_id: 's1',
        message_local_id: 1,
        message_create_time: ts(2026, 1, 5, 10, 0),
        target: group,
      },
    },
    orphans: [
      {
        revoked: true,
        kind: 'single',
        revoke_time: ts(2026, 1, 5, 10, 5),
        content: '孤立撤回样本',
        server_id: 'orphan-1',
        message_local_id: null,
        message_create_time: null,
        target: group,
      },
    ],
  }, null, 2), 'utf8')

  return {
    root,
    accountRoot,
    videoRoot,
    configPath,
    fixturePath,
    voicePath,
    voiceMapPath,
    stickerPath,
    stickerMapPath,
    combinedMapPath,
    revokeMapPath,
    outDir: join(root, 'out'),
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  }
}
