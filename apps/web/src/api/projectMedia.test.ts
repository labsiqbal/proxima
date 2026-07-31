import { describe, expect, it } from 'vitest'
import {
  collectArtboardMediaRefs,
  isAbsoluteMediaSrc,
  mergeProjectMediaRefs,
  needsRawDisplayBlob,
  projectMediaKey,
  resolveProjectMediaSrc,
} from './projectMedia'
import type { FileTarget } from '../types'

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
