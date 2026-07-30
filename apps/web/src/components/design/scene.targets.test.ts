import { describe, expect, it } from 'vitest'
import {
  reconcileSceneMediaTargets,
  type Scene,
} from './scene'
import type { FileTarget } from '../../types'

const trusted: FileTarget = {
  project: 'identity',
  area: { kind: 'ops', id: 42 },
  path: 'visual.png',
}

const forged: FileTarget = {
  project: 'identity',
  area: { kind: 'container', id: null },
  path: 'visual.png',
}

const scene = (layers: Scene['artboards'][number]['layers']): Scene => ({
  id: 'scene',
  type: 'graphic',
  title: 'Scene',
  artboards: [
    {
      id: 'artboard',
      width: 100,
      height: 100,
      background: '#fff',
      layers,
    },
  ],
})

describe('scene media target reconciliation', () => {
  it('keeps only trusted targets for unchanged layer identities and sources', () => {
    const previous = scene([
      {
        id: 'same-image',
        type: 'image',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        src: 'visual.png',
        target: trusted,
      },
      {
        id: 'changed-image',
        type: 'image',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        src: 'old.png',
        target: trusted,
      },
      {
        id: 'same-frame',
        type: 'rect',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        fill: '#fff',
        imageSrc: 'visual.png',
        imageTarget: trusted,
      },
      {
        id: 'changed-frame',
        type: 'ellipse',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        fill: '#fff',
        imageSrc: 'old.png',
        imageTarget: trusted,
      },
    ])
    const next = scene([
      {
        id: 'same-image',
        type: 'image',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        src: 'visual.png',
        target: forged,
      },
      {
        id: 'changed-image',
        type: 'image',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        src: 'new.png',
        target: forged,
      },
      {
        id: 'new-image',
        type: 'image',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        src: 'visual.png',
        target: forged,
      },
      {
        id: 'same-frame',
        type: 'star',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        fill: '#fff',
        imageSrc: 'visual.png',
        imageTarget: forged,
      },
      {
        id: 'changed-frame',
        type: 'ellipse',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        fill: '#fff',
        imageSrc: 'new.png',
        imageTarget: forged,
      },
    ])

    reconcileSceneMediaTargets(previous, next)

    const [
      sameImage,
      changedImage,
      newImage,
      sameFrame,
      changedFrame,
    ] = next.artboards[0].layers
    expect(sameImage).toMatchObject({ target: trusted })
    expect(changedImage).not.toHaveProperty('target')
    expect(newImage).not.toHaveProperty('target')
    expect(sameFrame).toMatchObject({ imageTarget: trusted })
    expect(changedFrame).not.toHaveProperty('imageTarget')
  })

  it('removes model targets when no trusted scene exists', () => {
    const next = scene([
      {
        id: 'image',
        type: 'image',
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        src: 'visual.png',
        target: forged,
      },
    ])

    reconcileSceneMediaTargets(null, next)

    expect(next.artboards[0].layers[0]).not.toHaveProperty('target')
  })
})
