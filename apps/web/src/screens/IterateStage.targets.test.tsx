import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({
  fsRead: vi.fn(),
  fileUrl: vi.fn(),
  rawUrl: vi.fn(),
  listArtifacts: vi.fn(),
  listMessages: vi.fn(),
  polling: new Map<number, () => Promise<unknown>>(),
  open: vi.fn(),
}))

vi.mock('../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    read: (...args: unknown[]) => mocks.fsRead(...args),
  })),
}))
vi.mock('../api/files', () => ({
  deleteSessionArtifact: vi.fn(),
  fileUrl: (...args: unknown[]) => mocks.fileUrl(...args),
  rawUrl: (...args: unknown[]) => mocks.rawUrl(...args),
  isSvgPath: (path: string) => /\.svg$/i.test(path),
  listSessionArtifacts: (...args: unknown[]) => mocks.listArtifacts(...args),
  retargetFile: (target: object, path: string) => ({ ...target, path }),
}))
vi.mock('../api/sessions', () => ({
  listMessages: (...args: unknown[]) => mocks.listMessages(...args),
}))
vi.mock('../api/runs', () => ({
  cancelRun: vi.fn(),
  deleteRun: vi.fn(),
}))
vi.mock('../api/workflows', () => ({
  getWorkflow: vi.fn(),
  updateWorkflow: vi.fn(),
}))
vi.mock('../hooks/usePolling', () => ({
  usePolling: (callback: () => Promise<unknown>, interval: number) => {
    mocks.polling.set(interval, callback)
  },
}))
vi.mock('../hooks/useEventStream', () => ({
  useEventStream: vi.fn(),
}))
vi.mock('../components/design/MiniPreview', () => ({
  MiniPreview: ({
    art,
    resolveSrc,
  }: {
    art?: { layers?: Array<{ src?: string; target?: unknown }> }
    resolveSrc: (src: string, target?: unknown) => string
  }) => {
    const image = art?.layers?.find(layer => layer.src)
    return image?.src
      ? <img alt="Design result thumbnail" src={resolveSrc(image.src, image.target)} />
      : null
  },
}))
vi.mock('../components/files/AppRunner', () => ({
  AppRunner: () => null,
}))
vi.mock('../components/chat/MessageContent', () => ({
  MessageContent: ({
    content,
    sourcePath,
    fileTarget,
  }: {
    content: string
    sourcePath?: string
    fileTarget?: unknown
  }) => (
    <div
      data-testid="iterate-markdown"
      data-source-path={sourcePath}
      data-file-target={JSON.stringify(fileTarget)}
    >
      {content}
    </div>
  ),
}))
vi.mock('../components/chat/runError', () => ({
  formatRunError: (value: string) => value,
}))
vi.mock('../components/ui/Dialog', () => ({
  confirmDialog: vi.fn(),
}))

import { IterateStage } from './IterateStage'

describe('IterateStage canonical artifact targets', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.polling.clear()
    mocks.listMessages.mockResolvedValue({ messages: [] })
    mocks.fileUrl.mockImplementation(
      (_slug: string, path: string, target?: { area?: { kind?: string; id?: number } }) =>
        `/file/${target?.area?.kind || 'legacy'}/${target?.area?.id || 'root'}/${path}`,
    )
    mocks.rawUrl.mockImplementation(
      (slug: string, path: string, target?: object) =>
        target
          ? `/api/projects/${slug}/raw?target=${encodeURIComponent(JSON.stringify(target))}`
          : `/api/projects/${slug}/raw?path=${encodeURIComponent(path)}`,
    )
    mocks.fsRead.mockResolvedValue({
      content: '![Chart](images/chart.png)',
    })
    vi.stubGlobal('open', mocks.open)
  })

  it('keeps the source target when rendering a Markdown result', async () => {
    const target = {
      project: 'identity',
      area: { kind: 'ops', id: 42 },
      path: 'reports/brief.md',
    }
    mocks.listArtifacts.mockResolvedValue({
      artifacts: [
        {
          type: 'doc',
          title: 'Brief',
          path: 'brief.md',
          target,
        },
      ],
    })
    render(
      <IterateStage
        token="token"
        workflowId={3}
        sessionId={7}
        projectSlug="identity"
      />,
    )

    await act(async () => {
      await mocks.polling.get(4000)?.()
    })
    await userEvent.click(screen.getByRole('button', { name: /Result/ }))
    await userEvent.click(screen.getByRole('button', { name: /Brief/ }))

    expect(mocks.fsRead).toHaveBeenCalledWith(target)
    const markdown = await screen.findByTestId('iterate-markdown')
    expect(markdown).toHaveAttribute('data-source-path', 'reports/brief.md')
    expect(JSON.parse(markdown.getAttribute('data-file-target') || '{}')).toEqual(target)
  })

  it('forwards scene image targets through design thumbnails', async () => {
    const designTarget = {
      project: 'identity',
      area: { kind: 'ops', id: 42 },
      path: 'artifacts/design/canonical',
    }
    const imageTarget = {
      project: 'identity',
      area: { kind: 'ops', id: 42 },
      path: 'visual.png',
    }
    mocks.listArtifacts.mockResolvedValue({
      artifacts: [
        {
          id: 'canonical',
          type: 'design',
          title: 'Canonical design',
          path: 'artifacts/design/canonical',
          target: designTarget,
        },
      ],
    })
    mocks.fsRead.mockResolvedValue({
      content: JSON.stringify({
        id: 'canonical',
        type: 'graphic',
        title: 'Canonical design',
        artboards: [
          {
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
                width: 100,
                height: 100,
                src: 'visual.png',
                target: imageTarget,
              },
            ],
          },
        ],
      }),
    })
    render(
      <IterateStage
        token="token"
        workflowId={3}
        sessionId={7}
        projectSlug="identity"
      />,
    )

    await act(async () => {
      await mocks.polling.get(4000)?.()
    })
    await userEvent.click(screen.getByRole('button', { name: /Result/ }))

    expect(await screen.findByRole('img', { name: 'Design result thumbnail' }))
      .toHaveAttribute('src', '/file/ops/42/visual.png')
    expect(mocks.fileUrl).toHaveBeenCalledWith(
      'identity',
      'visual.png',
      imageTarget,
    )
  })

  it('opens SVG results through the authenticated raw endpoint', async () => {
    const target = {
      project: 'identity',
      area: { kind: 'ops', id: 42 },
      path: 'brand/mark.svg',
    }
    mocks.listArtifacts.mockResolvedValue({
      artifacts: [
        {
          type: 'file',
          title: 'Mark',
          path: 'brand/mark.svg',
          target,
        },
      ],
    })
    render(
      <IterateStage
        token="token"
        workflowId={3}
        sessionId={7}
        projectSlug="identity"
      />,
    )

    await act(async () => {
      await mocks.polling.get(4000)?.()
    })
    await userEvent.click(screen.getByRole('button', { name: /Result/ }))
    await userEvent.click(screen.getByRole('button', { name: /Mark/ }))

    expect(mocks.rawUrl).toHaveBeenCalledWith('identity', 'brand/mark.svg', target)
    expect(mocks.fileUrl).not.toHaveBeenCalledWith('identity', 'brand/mark.svg', target)
    expect(mocks.open).toHaveBeenCalledWith(
      `/api/projects/identity/raw?target=${encodeURIComponent(JSON.stringify(target))}`,
      '_blank',
    )
  })
})
