import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MessageContent } from './MessageContent'

const mocks = vi.hoisted(() => ({
  fetchRawBlob: vi.fn(),
  fileUrl: vi.fn(),
  rawUrl: vi.fn(),
  relativeFileUrl: vi.fn(),
  relativeRawUrl: vi.fn(),
  resolveRelativeReference: vi.fn(),
  retargetFile: vi.fn((target: object, path: string) => ({ ...target, path })),
  isSvgPath: (path: string) => /\.svg$/i.test(path),
}))

vi.mock('../../api/files', () => ({
  fetchRawBlob: (...args: unknown[]) => mocks.fetchRawBlob(...args),
  fileUrl: (...args: unknown[]) => mocks.fileUrl(...args),
  rawUrl: (...args: unknown[]) => mocks.rawUrl(...args),
  relativeFileUrl: (...args: unknown[]) => mocks.relativeFileUrl(...args),
  relativeRawUrl: (...args: unknown[]) => mocks.relativeRawUrl(...args),
  resolveRelativeReference: (...args: unknown[]) => mocks.resolveRelativeReference(...args),
  retargetFile: (...args: [object, string]) => mocks.retargetFile(...args),
  isSvgPath: mocks.isSvgPath,
}))

describe('MessageContent project file resources', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fileUrl.mockImplementation((_slug: string, path: string) => `/preview/${path}`)
    mocks.rawUrl.mockImplementation((slug: string, path: string, target?: { path?: string }) => (
      target
        ? `/api/projects/${slug}/raw?target=${encodeURIComponent(JSON.stringify(target))}`
        : `/api/projects/${slug}/raw?path=${encodeURIComponent(path)}`
    ))
    mocks.relativeFileUrl.mockImplementation((
      _slug: string,
      reference: string,
      _source: string,
    ) => `/preview-rel/${reference}`)
    mocks.relativeRawUrl.mockImplementation((
      slug: string,
      reference: string,
      _source: string,
      target?: { path?: string },
    ) => (
      target
        ? `/api/projects/${slug}/raw?target=${encodeURIComponent(JSON.stringify({ ...target, path: `resolved/${reference}` }))}`
        : `/api/projects/${slug}/raw?path=resolved/${reference}`
    ))
    mocks.resolveRelativeReference.mockImplementation((reference: string, sourcePath: string) => {
      const base = sourcePath.split('/').slice(0, -1)
      return [...base, reference].join('/')
    })
    mocks.fetchRawBlob.mockResolvedValue('blob:md-svg')
  })

  it('routes download chips through the authenticated raw endpoint', () => {
    const target = {
      project: 'demo',
      area: { kind: 'ops' as const, id: 9 },
      path: 'notes/brief.md',
    }
    render(
      <MessageContent
        content="Grab [site](export/index.html) when ready."
        token="token"
        slug="demo"
        sourcePath={target.path}
        fileTarget={target}
      />,
    )

    const chip = screen.getByRole('link', { name: 'index.html' })
    expect(chip).toHaveAttribute('download', 'index.html')
    expect(mocks.relativeRawUrl).toHaveBeenCalledWith(
      'demo',
      'export/index.html',
      target.path,
      target,
    )
    expect(chip.getAttribute('href') || '').toContain('/api/projects/demo/raw?')
    expect(chip.getAttribute('href') || '').not.toContain('/preview')
    expect(mocks.relativeFileUrl).not.toHaveBeenCalled()
  })

  it('renders SVG images from authenticated raw bytes', async () => {
    const target = {
      project: 'demo',
      area: { kind: 'ops' as const, id: 9 },
      path: 'notes/brief.md',
    }
    render(
      <MessageContent
        content="Logo ![Mark](assets/mark.svg)"
        token="token"
        slug="demo"
        sourcePath={target.path}
        fileTarget={target}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'Mark' })).toHaveAttribute('src', 'blob:md-svg')
    })
    expect(mocks.resolveRelativeReference).toHaveBeenCalledWith('assets/mark.svg', target.path)
    expect(mocks.fetchRawBlob).toHaveBeenCalledWith(
      'token',
      'demo',
      'notes/assets/mark.svg',
      { ...target, path: 'notes/assets/mark.svg' },
    )
    expect(mocks.relativeFileUrl).not.toHaveBeenCalled()
  })
})
