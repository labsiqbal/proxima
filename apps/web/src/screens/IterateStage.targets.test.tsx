import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({
  fsRead: vi.fn(),
  listArtifacts: vi.fn(),
  listMessages: vi.fn(),
  polling: new Map<number, () => Promise<unknown>>(),
}))

vi.mock('../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    read: (...args: unknown[]) => mocks.fsRead(...args),
  })),
}))
vi.mock('../api/files', () => ({
  deleteSessionArtifact: vi.fn(),
  fileUrl: vi.fn((_slug: string, path: string) => `/file/${path}`),
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
  MiniPreview: () => null,
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
    mocks.fsRead.mockResolvedValue({
      content: '![Chart](images/chart.png)',
    })
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
})
