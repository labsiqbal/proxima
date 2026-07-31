import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FileTarget } from '../types'

const mocks = vi.hoisted(() => ({
  fetchRawFile: vi.fn(),
  fileUrl: vi.fn((slug: string, path: string) => `/api/projects/${slug}/preview?path=${encodeURIComponent(path)}`),
  isSvgPath: (path: string) => /\.svg$/i.test(path),
  revoke: vi.fn(),
  createObjectURL: vi.fn((blob: Blob) => `blob:${blob.type || 'object'}`),
}))

vi.mock('./files', () => ({
  fetchRawFile: (...args: unknown[]) => mocks.fetchRawFile(...args),
  fileUrl: (...args: unknown[]) => mocks.fileUrl(...(args as [string, string])),
  isSvgPath: mocks.isSvgPath,
}))

import {
  blobToDataUrl,
  collectArtboardMediaRefs,
  isAbsoluteMediaSrc,
  loadProjectMediaBlob,
  measureImageBlob,
  measureProjectMedia,
  mergeProjectMediaRefs,
  needsRawDisplayBlob,
  projectMediaDataUrl,
  projectMediaKey,
  resolveProjectMediaSrc,
} from './projectMedia'

const target: FileTarget = {
  project: 'identity',
  area: { kind: 'ops', id: 42 },
  path: 'visual.png',
}

describe('project media display resolution', () => {
  it('keys path-only and target media distinctly', () => {
    expect(projectMediaKey('brand/mark.svg')).toBe(JSON.stringify(['path', 'brand/mark.svg']))
    expect(projectMediaKey('visual.png', target)).toBe(JSON.stringify([
      'target',
      'visual.png',
      'identity',
      'ops',
      42,
      'visual.png',
    ]))
  })

  it('requires raw bytes for SVG and any target-bound media', () => {
    expect(needsRawDisplayBlob('brand/mark.svg')).toBe(true)
    expect(needsRawDisplayBlob('visual.png', target)).toBe(true)
    expect(needsRawDisplayBlob('visual.png')).toBe(false)
    expect(needsRawDisplayBlob('https://cdn.example/a.svg')).toBe(false)
    expect(isAbsoluteMediaSrc('blob:abc')).toBe(true)
  })

  it('collects target frames and path-only SVG layers from an artboard', () => {
    const refs = collectArtboardMediaRefs({
      layers: [
        {
          type: 'image',
          src: 'visual.png',
          target,
        },
        {
          type: 'image',
          src: 'legacy/mark.svg',
        },
        {
          type: 'image',
          src: 'photo.png',
        },
        {
          type: 'rect',
          imageSrc: 'frame.svg',
        },
      ],
    })
    expect(refs).toEqual([
      { src: 'visual.png', target },
      { src: 'legacy/mark.svg', target: undefined },
      { src: 'frame.svg', target: undefined },
    ])
  })

  it('resolves SVG and targets from hydrated blob urls only', () => {
    const urls = {
      [projectMediaKey('brand/mark.svg')]: 'blob:svg',
      [projectMediaKey('visual.png', target)]: 'blob:target',
    }
    expect(resolveProjectMediaSrc('brand/mark.svg', undefined, 'identity', urls)).toBe('blob:svg')
    expect(resolveProjectMediaSrc('visual.png', target, 'identity', urls)).toBe('blob:target')
    expect(resolveProjectMediaSrc('brand/mark.svg', undefined, 'identity', {})).toBe('')
    expect(resolveProjectMediaSrc('photo.png', undefined, 'identity', urls)).toContain('/api/')
    expect(resolveProjectMediaSrc('https://cdn.example/a.png', undefined, 'identity', urls))
      .toBe('https://cdn.example/a.png')
  })

  it('merges and dedupes media refs', () => {
    const merged = mergeProjectMediaRefs(
      [{ src: 'a.svg' }, { src: 'visual.png', target }],
      [{ src: 'a.svg' }, { src: 'photo.png' }],
    )
    expect(merged).toEqual([
      { src: 'a.svg', target: undefined },
      { src: 'visual.png', target },
    ])
  })
})

describe('project media byte loading', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchRawFile.mockResolvedValue(new Blob(['<svg/>'], { type: 'image/svg+xml' }))
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: mocks.createObjectURL,
      revokeObjectURL: mocks.revoke,
    })
  })

  it('loads SVG and target media through authenticated raw bytes', async () => {
    await loadProjectMediaBlob('token', 'identity', 'legacy/mark.svg')
    expect(mocks.fetchRawFile).toHaveBeenCalledWith(
      'token',
      'identity',
      'legacy/mark.svg',
      undefined,
    )

    mocks.fetchRawFile.mockClear()
    await loadProjectMediaBlob('token', 'identity', 'visual.png', target)
    expect(mocks.fetchRawFile).toHaveBeenCalledWith(
      'token',
      'identity',
      'visual.png',
      target,
    )
  })

  it('measures raw SVG bytes and revokes the temporary object URL', async () => {
    const blob = new Blob(['<svg/>'], { type: 'image/svg+xml' })
    class FakeImage {
      naturalWidth = 64
      naturalHeight = 32
      width = 64
      height = 32
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      set src(_value: string) {
        queueMicrotask(() => this.onload?.())
      }
    }
    vi.stubGlobal('Image', FakeImage as unknown as typeof Image)

    await expect(measureImageBlob(blob)).resolves.toEqual({ w: 64, h: 32 })
    expect(mocks.createObjectURL).toHaveBeenCalledWith(blob)
    expect(mocks.revoke).toHaveBeenCalledWith('blob:image/svg+xml')
  })

  it('fails promptly when SVG decode rejects instead of hanging on empty src', async () => {
    class BrokenImage {
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      set src(_value: string) {
        queueMicrotask(() => this.onerror?.())
      }
    }
    vi.stubGlobal('Image', BrokenImage as unknown as typeof Image)

    await expect(measureImageBlob(new Blob(['x']))).rejects.toThrow(/decode/i)
    expect(mocks.revoke).toHaveBeenCalled()
  })

  it('exports path-only SVG via raw bytes even before resolveSrc hydrates', async () => {
    const blob = new Blob(['<svg id="mark"/>'], { type: 'image/svg+xml' })
    mocks.fetchRawFile.mockResolvedValueOnce(blob)
    const dataUrl = await projectMediaDataUrl(
      'token',
      'identity',
      'artifacts/design/_assets/icon.svg',
      undefined,
      '',
    )
    expect(mocks.fetchRawFile).toHaveBeenCalledWith(
      'token',
      'identity',
      'artifacts/design/_assets/icon.svg',
      undefined,
    )
    expect(dataUrl.startsWith('data:image/svg+xml')).toBe(true)
  })

  it('rejects empty media sources instead of assigning blank image src', async () => {
    await expect(measureProjectMedia('token', 'identity', '')).rejects.toThrow(/missing media source/i)
    await expect(loadProjectMediaBlob('token', 'identity', '')).rejects.toThrow(/missing media source/i)
    await expect(projectMediaDataUrl('token', 'identity', '')).resolves.toBe('')
  })

  it('turns blobs into data urls for export', async () => {
    await expect(blobToDataUrl(new Blob(['hi'], { type: 'text/plain' })))
      .resolves.toMatch(/^data:text\/plain/)
  })
})
