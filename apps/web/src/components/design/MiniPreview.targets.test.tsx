import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { MiniPreview } from './MiniPreview'
import type { Artboard } from './scene'

describe('MiniPreview image targets', () => {
  it('passes a persisted file target to the scene media resolver', () => {
    const target = {
      project: 'identity',
      area: { kind: 'ops' as const, id: 42 },
      path: 'visual.png',
    }
    const art: Artboard = {
      id: 'a1',
      width: 100,
      height: 100,
      background: '#fff',
      layers: [
        {
          id: 'image',
          type: 'image',
          x: 0,
          y: 0,
          width: 100,
          height: 100,
          src: 'visual.png',
          target,
        },
      ],
    }
    const resolveSrc = vi.fn(() => '/api/target-preview/identity/ops/42/visual.png')

    const { container } = render(
      <MiniPreview art={art} resolveSrc={resolveSrc} />,
    )

    expect(resolveSrc).toHaveBeenCalledWith('visual.png', target)
    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      '/api/target-preview/identity/ops/42/visual.png',
    )
  })

  it('draws nothing for an unresolvable source instead of a broken image', () => {
    const art: Artboard = {
      id: 'a1',
      width: 100,
      height: 100,
      background: '#fff',
      layers: [
        { id: 'image', type: 'image', x: 0, y: 0, width: 100, height: 100, src: 'gen:pending' },
      ],
    }

    const { container } = render(<MiniPreview art={art} resolveSrc={() => ''} />)

    expect(container.querySelector('img')).toBeNull()
  })
})
