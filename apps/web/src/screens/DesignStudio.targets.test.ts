import { describe, expect, it } from 'vitest'
import {
  collectDesignTargetMedia,
  designTargetMediaKey,
} from './DesignStudio'
import type { Scene } from '../components/design/scene'
import type { FileTarget } from '../types'

const target: FileTarget = {
  project: 'identity',
  area: { kind: 'ops', id: 42 },
  path: 'visual.png',
}

describe('Design Studio canonical image bytes', () => {
  it('collects trusted canvas, frame, path-only SVG, and asset SVG media', () => {
    const scene: Scene = {
      id: 'scene',
      type: 'graphic',
      title: 'Canonical',
      artboards: [{
        id: 'artboard',
        width: 100,
        height: 100,
        background: '#fff',
        layers: [
          {
            id: 'image',
            type: 'image',
            x: 0,
            y: 0,
            width: 50,
            height: 50,
            src: 'visual.png',
            target,
          },
          {
            id: 'frame',
            type: 'rect',
            x: 50,
            y: 0,
            width: 50,
            height: 50,
            fill: '#fff',
            imageSrc: 'visual.png',
            imageTarget: target,
          },
          {
            id: 'legacy-svg',
            type: 'image',
            x: 0,
            y: 50,
            width: 50,
            height: 50,
            src: 'legacy/mark.svg',
          },
          {
            id: 'photo',
            type: 'image',
            x: 50,
            y: 50,
            width: 50,
            height: 50,
            src: 'photo.png',
          },
        ],
      }],
    }

    const references = collectDesignTargetMedia(
      scene,
      [{ art: scene.artboards[0] }],
      ['artifacts/design/_assets/icon.svg', 'artifacts/design/_assets/photo.png'],
    )

    expect(references).toEqual([
      { src: 'visual.png', target },
      { src: 'legacy/mark.svg', target: undefined },
      { src: 'artifacts/design/_assets/icon.svg', target: undefined },
    ])
    expect(designTargetMediaKey('visual.png', target)).toBe(
      JSON.stringify(['target', 'visual.png', 'identity', 'ops', 42, 'visual.png']),
    )
    expect(designTargetMediaKey('legacy/mark.svg')).toBe(
      JSON.stringify(['path', 'legacy/mark.svg']),
    )
  })
})
