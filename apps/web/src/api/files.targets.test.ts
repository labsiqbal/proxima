import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => vi.fn())

vi.mock('./client', () => ({
  api: apiMock,
}))

import {
  deleteSessionArtifact,
  previewUrl,
  relativeFileUrl,
} from './files'
import type { FileTarget } from '../types'

const target: FileTarget = {
  project: 'identity',
  area: { kind: 'ops', id: 42 },
  path: 'reports/brief.md',
}

beforeEach(() => {
  apiMock.mockReset()
  apiMock.mockResolvedValue({ ok: true })
})

describe('canonical file URLs', () => {
  it('keeps the Area identity in the preview path namespace', () => {
    expect(previewUrl('identity', 'display/brief.md', target)).toBe(
      '/api/target-preview/identity/ops/42/reports/brief.md',
    )
  })

  it('resolves Markdown siblings relative to the originating Area', () => {
    expect(
      relativeFileUrl(
        'identity',
        'images/chart.png?v=7#figure',
        target.path,
        target,
      ),
    ).toBe(
      '/api/target-preview/identity/ops/42/reports/images/chart.png?v=7#figure',
    )
    expect(
      relativeFileUrl('identity', '../../escape.png', target.path, target),
    ).toBe('')
  })

  it('deletes a session artifact by canonical target', async () => {
    await deleteSessionArtifact('token', 7, target)

    const [url, token, init] = apiMock.mock.calls[0]
    expect(token).toBe('token')
    expect(init).toEqual({ method: 'DELETE' })
    const query = new URLSearchParams(String(url).split('?')[1])
    expect(JSON.parse(query.get('target') || '{}')).toEqual(target)
    expect(query.has('path')).toBe(false)
  })
})
