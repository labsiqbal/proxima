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
  it('collects trusted canvas, frame, and gallery media for blob hydration', () => {
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
        ],
      }],
    }

    const references = collectDesignTargetMedia(scene, [
      { art: scene.artboards[0] },
    ])

    expect(references).toEqual([{ src: 'visual.png', target }])
    expect(designTargetMediaKey('visual.png', target)).toBe(
      JSON.stringify(['visual.png', target]),
    )
  })
})
