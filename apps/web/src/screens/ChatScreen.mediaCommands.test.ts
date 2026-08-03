import { describe, expect, it } from 'vitest'

import { MEDIA_COMMAND_RE } from './ChatScreen'

/**
 * A media slash command must fall through ChatScreen's local command dispatch and
 * reach the backend, which routes it to the selected generation provider. When
 * this regex misses a command the UI answers "Unknown command" and nothing is
 * generated - which is exactly how /video shipped broken once. The list must stay
 * in step with `_chat_media_kind` + `ALIASES` in the API.
 */
describe('MEDIA_COMMAND_RE', () => {
  it.each([
    '/image a neon mascot',
    '/gambar kucing oranye',
    '/video a cat stretching on a sofa',
    '/klip kucing meregang',
    '/design a launch poster',
    '/image-studio',
    '/design-studio',
  ])('lets %s reach the backend', command => {
    expect(MEDIA_COMMAND_RE.test(command)).toBe(true)
  })

  it('matches a bare command with no brief (the clarify-form path)', () => {
    expect(MEDIA_COMMAND_RE.test('/video')).toBe(true)
    expect(MEDIA_COMMAND_RE.test('/VIDEO')).toBe(true)
  })

  it('does not swallow local or unrelated commands', () => {
    for (const command of ['/help', '/status', '/new', '/goal ship it', '/videos', '/imagery']) {
      expect(MEDIA_COMMAND_RE.test(command)).toBe(false)
    }
  })

  it('leaves a raw-passthrough // prompt alone', () => {
    expect(MEDIA_COMMAND_RE.test('//video literal text for the agent')).toBe(false)
  })
})
