import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { projectFs } from '../../api/fsAdapter'
import { ArtifactThumb, artifactKind } from './ArtifactThumb'
import type { Artifact } from '../../api/files'

vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({ read: vi.fn().mockResolvedValue({ content: '{}' }) })),
}))
vi.mock('../../hooks/useRawBlobUrl', () => ({
  useRawBlobUrl: (_token: string | undefined, _slug: string | undefined, path: string) => ({
    url: path ? `blob:${path}` : null,
    status: 'ready',
    retry: vi.fn(),
  }),
}))
vi.mock('../../hooks/useProjectMediaUrls', () => ({ useProjectMediaUrls: () => ({}) }))
vi.mock('../design/MiniPreview', () => ({
  MiniPreview: ({ art }: { art?: { id?: string } }) => <span data-testid="mini">mini:{art?.id}</span>,
}))

const artifact = (over: Partial<Artifact>): Artifact => ({ type: 'file', title: 't', path: 'p', ...over })

describe('artifactKind', () => {
  it('sorts artifacts into what you look at and what you read', () => {
    expect(artifactKind(artifact({ type: 'design', path: 'artifacts/design/a' }))).toBe('design')
    expect(artifactKind(artifact({ type: 'image', path: 'a.png' }))).toBe('image')
    expect(artifactKind(artifact({ type: 'file', path: 'a.webp' }))).toBe('image')
    expect(artifactKind(artifact({ type: 'video-file', path: 'a.mp4' }))).toBe('video')
    expect(artifactKind(artifact({ type: 'file', path: 'a.mov' }))).toBe('video')
    expect(artifactKind(artifact({ type: 'doc', path: 'a.md' }))).toBe('document')
    expect(artifactKind(artifact({ type: 'page', path: 'a.html' }))).toBe('document')
    expect(artifactKind(artifact({ type: 'file', path: 'a.csv' }))).toBe('document')
  })
})

describe('ArtifactThumb', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders a raster image straight from the preview endpoint', () => {
    render(<ArtifactThumb token="t" slug="alpha" artifact={artifact({ type: 'image', title: 'shot.png', path: 'artifacts/shot.png' })} />)
    const img = screen.getByRole('presentation')
    expect(img).toHaveAttribute('src', '/api/preview/alpha/artifacts/shot.png')
    expect(img).toHaveAttribute('loading', 'lazy')
  })

  it('renders SVG through authenticated raw bytes, never the inert preview', () => {
    render(<ArtifactThumb token="t" slug="alpha" artifact={artifact({ type: 'image', title: 'logo.svg', path: 'artifacts/logo.svg' })} />)
    expect(screen.getByRole('presentation')).toHaveAttribute('src', 'blob:artifacts/logo.svg')
  })

  it('renders a video as a metadata-only poster frame', () => {
    const { container } = render(<ArtifactThumb token="t" slug="alpha" artifact={artifact({ type: 'video-file', title: 'cut.mp4', path: 'artifacts/cut.mp4' })} />)
    const video = container.querySelector('video')
    expect(video).toHaveAttribute('preload', 'metadata')
    expect(video?.getAttribute('src')).toBe('/api/preview/alpha/artifacts/cut.mp4#t=0.1')
  })

  it('renders a design as its first artboard', async () => {
    vi.mocked(projectFs).mockReturnValue({
      read: vi.fn().mockResolvedValue({ content: JSON.stringify({ artboards: [{ id: 'ab1', width: 100, height: 100, layers: [] }] }) }),
    } as never)
    render(<ArtifactThumb token="t" slug="alpha" artifact={artifact({ type: 'design', title: 'Poster', path: 'artifacts/design/poster' })} />)
    expect(await screen.findByTestId('mini')).toHaveTextContent('mini:ab1')
  })

  it('falls back to the type glyph when a design has no readable scene', async () => {
    vi.mocked(projectFs).mockReturnValue({ read: vi.fn().mockRejectedValue(new Error('nope')) } as never)
    render(<ArtifactThumb token="t" slug="alpha" artifact={artifact({ type: 'design', title: 'Poster', path: 'artifacts/design/poster' })} />)
    await waitFor(() => expect(screen.getByTestId('artifact-glyph')).toBeInTheDocument())
  })
})
