import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => vi.fn())

vi.mock('./client', () => ({
  api: apiMock,
}))

import {
  deleteSessionArtifact,
  fetchRawFile,
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

afterEach(() => {
  vi.unstubAllGlobals()
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

  it('fetches canonical raw bytes with owner authentication', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      'canonical-image',
      { headers: { 'Content-Type': 'image/png' } },
    ))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchRawFile(
      'owner-token',
      'identity',
      'visual.png',
      {
        ...target,
        path: 'visual.png',
      },
    )
    expect(await result.text()).toBe('canonical-image')
    expect(result.type).toBe('image/png')

    const [url, init] = fetchMock.mock.calls[0]
    const query = new URLSearchParams(String(url).split('?')[1])
    expect(JSON.parse(query.get('target') || '{}')).toEqual({
      ...target,
      path: 'visual.png',
    })
    expect(init).toEqual({
      headers: { Authorization: 'Bearer owner-token' },
    })
  })
})
