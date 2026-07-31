import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { projectMediaKey } from '../api/projectMedia'

const mocks = vi.hoisted(() => ({
  fetchRawBlob: vi.fn(),
  revoke: vi.fn(),
}))

vi.mock('../api/files', () => ({
  fetchRawBlob: (...args: unknown[]) => mocks.fetchRawBlob(...args),
  isSvgPath: (path: string) => /\.svg$/i.test(path),
  fileUrl: (slug: string, path: string) => `/api/projects/${slug}/raw?path=${encodeURIComponent(path)}`,
}))

import { useProjectMediaUrls } from './useProjectMediaUrls'

describe('useProjectMediaUrls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchRawBlob.mockImplementation(
      async (_token: string, _slug: string, path: string) => `blob:${path}`,
    )
    vi.stubGlobal('URL', {
      ...URL,
      revokeObjectURL: mocks.revoke,
    })
  })

  it('hydrates target and path-only SVG refs, then revokes on cleanup', async () => {
    const target = {
      project: 'identity',
      area: { kind: 'ops' as const, id: 42 },
      path: 'visual.png',
    }
    const { result, unmount } = renderHook(() => useProjectMediaUrls('token', 'identity', [
      { src: 'visual.png', target },
      { src: 'legacy/mark.svg' },
      { src: 'photo.png' },
    ]))

    await waitFor(() => {
      expect(result.current[projectMediaKey('visual.png', target)]).toBe('blob:visual.png')
      expect(result.current[projectMediaKey('legacy/mark.svg')]).toBe('blob:legacy/mark.svg')
    })
    expect(result.current[projectMediaKey('photo.png')]).toBeUndefined()
    expect(mocks.fetchRawBlob).toHaveBeenCalledWith('token', 'identity', 'visual.png', target)
    expect(mocks.fetchRawBlob).toHaveBeenCalledWith(
      'token',
      'identity',
      'legacy/mark.svg',
      undefined,
    )
    expect(mocks.fetchRawBlob).not.toHaveBeenCalledWith(
      'token',
      'identity',
      'photo.png',
      undefined,
    )

    unmount()
    expect(mocks.revoke).toHaveBeenCalledWith('blob:visual.png')
    expect(mocks.revoke).toHaveBeenCalledWith('blob:legacy/mark.svg')
  })
})
