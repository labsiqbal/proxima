import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => vi.fn())

vi.mock('./client', () => ({
  api: apiMock,
}))

import {
  deleteSessionArtifact,
  fetchRawFile,
  isSvgPath,
  previewUrl,
  rawUrl,
  relativeFileUrl,
  relativeRawUrl,
  setTargetPreviewMode,
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

  it('adds scoped active authority only when explicitly supplied', () => {
    const active = previewUrl('identity', 'display/brief.md', target, {
      previewSession: 's'.repeat(32),
    })
    const [path, rawQuery] = active.split('?')
    expect(path).toBe('/api/target-preview/identity/ops/42/reports/brief.md')
    expect(Object.fromEntries(new URLSearchParams(rawQuery))).toEqual({
      __proxima_mode: 'active',
      __proxima_preview_session: 's'.repeat(32),
    })
  })

  it('changes active mode through a bearer-authenticated target mutation', async () => {
    await setTargetPreviewMode(
      'owner-token',
      'identity',
      target,
      's'.repeat(32),
      false,
    )

    const [url, token, init] = apiMock.mock.calls[0]
    expect(token).toBe('owner-token')
    expect(init).toEqual({
      method: 'POST',
      keepalive: false,
      body: JSON.stringify({
        active: false,
        preview_session: 's'.repeat(32),
      }),
    })
    const query = new URLSearchParams(String(url).split('?')[1])
    expect(JSON.parse(query.get('target') || '{}')).toEqual(target)
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

  it('builds authenticated raw download URLs without the preview entry', () => {
    const activeHtml = {
      ...target,
      path: 'site/index.html',
    }
    expect(rawUrl('identity', 'site/index.html', activeHtml)).toBe(
      `/api/projects/identity/raw?target=${encodeURIComponent(JSON.stringify(activeHtml))}`,
    )
    expect(rawUrl('identity', 'notes.bin')).toBe(
      '/api/projects/identity/raw?path=notes.bin',
    )
    expect(rawUrl('identity', 'site/index.html', activeHtml)).not.toContain(
      '/api/target-preview/',
    )
  })

  it('resolves Markdown sibling downloads onto the raw endpoint', () => {
    expect(
      relativeRawUrl(
        'identity',
        'export/report.html',
        target.path,
        target,
      ),
    ).toBe(
      `/api/projects/identity/raw?target=${encodeURIComponent(JSON.stringify({
        ...target,
        path: 'reports/export/report.html',
      }))}`,
    )
    expect(
      relativeRawUrl('identity', '../../escape.html', target.path, target),
    ).toBe('')
    expect(relativeRawUrl('identity', 'diagram.svg', target.path, target)).not.toContain(
      '/api/target-preview/',
    )
  })

  it('detects SVG paths including query suffixes', () => {
    expect(isSvgPath('brand/logo.svg')).toBe(true)
    expect(isSvgPath('brand/logo.SVG?v=2')).toBe(true)
    expect(isSvgPath('brand/logo.png')).toBe(false)
  })
})
