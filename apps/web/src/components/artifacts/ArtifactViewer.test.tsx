import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { ArtifactViewer } from './ArtifactViewer'
import { previewUrl } from '../../api/files'

const fsRead = vi.fn()
const setTargetPreviewMode = vi.fn()

vi.mock('../../api/files', () => ({
  previewUrl: vi.fn((_slug: string, path: string, _target?: unknown, active?: { generation: string }) => `/preview/${path}${active ? `?generation=${active.generation}` : ''}`),
  setTargetPreviewMode: (...args: unknown[]) => setTargetPreviewMode(...args),
}))
vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({ read: (...args: unknown[]) => fsRead(...args) })),
}))
vi.mock('../chat/MessageContent', () => ({
  MessageContent: ({ content, sourcePath, fileTarget }: {
    content: string
    sourcePath?: string
    fileTarget?: unknown
  }) => <div
    data-testid="message-content"
    data-source-path={sourcePath}
    data-file-target={JSON.stringify(fileTarget)}
  >{content}</div>,
}))
vi.mock('./MermaidDiagram', () => ({
  MermaidDiagram: ({ source, onEdit }: { source: string; onEdit: () => void }) => <button type="button" onClick={onEdit}>Edit diagram {source}</button>,
}))
vi.mock('./ExcalidrawWhiteboard', () => ({
  ExcalidrawWhiteboard: ({ onClose, onSaved }: { onClose: () => void; onSaved: (path: string) => void }) => <div data-testid="whiteboard">
    <button type="button" onClick={() => onSaved('artifacts/whiteboards/flow.excalidraw')}>Save whiteboard</button>
    <button type="button" onClick={onClose}>Back to artifact</button>
  </div>,
}))

beforeEach(() => {
  fsRead.mockReset()
  setTargetPreviewMode.mockReset()
  window.localStorage.clear()
})

describe('ArtifactViewer v2 review flow', () => {
  it('is a modal dialog that traps focus, closes with Escape, and restores its trigger', async () => {
    function Harness() {
      const [open, setOpen] = React.useState(false)
      return <>
        <button type="button" onClick={() => setOpen(true)}>Review artifact</button>
        {open && <ArtifactViewer
          token="token"
          slug="master"
          items={[{ type: 'image', title: 'Hero', path: 'artifacts/hero.png' }]}
          index={0}
          onIndex={() => undefined}
          onClose={() => setOpen(false)}
        />}
      </>
    }

    const view = render(<Harness />)
    await userEvent.click(view.getByRole('button', { name: 'Review artifact' }))

    const dialog = screen.getByRole('dialog', { name: 'Artifact review: Hero' })
    const close = screen.getByRole('button', { name: 'Close artifact review' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleDescription('Review this artifact and add editable feedback to its producing chat.')
    await waitFor(() => expect(close).toHaveFocus())

    const feedback = screen.getByRole('textbox', { name: 'General feedback' })
    feedback.focus()
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'Annotate' })).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(feedback).toHaveFocus()

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Artifact review: Hero' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Review artifact' })).toHaveFocus())
  })

  it('closes only after a successful feedback handoff', async () => {
    const successfulHandoff = vi.fn().mockResolvedValue({ ok: true })
    const failedHandoff = vi.fn().mockResolvedValue({
      ok: false,
      message: 'The producing chat is no longer available.',
    })
    const onClose = vi.fn()
    const view = render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'image', title: 'Hero', path: 'artifacts/hero.png' }]}
      index={0}
      onIndex={() => undefined}
      onClose={onClose}
      reviewSessionId={7}
      onSendFeedback={failedHandoff}
    />)

    await userEvent.type(screen.getByRole('textbox', { name: 'General feedback' }), 'Use the approved logo.')
    await userEvent.click(screen.getByRole('button', { name: 'Add feedback to chat' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('producing chat is no longer available')
    expect(onClose).not.toHaveBeenCalled()

    view.rerender(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'image', title: 'Hero', path: 'artifacts/hero.png' }]}
      index={0}
      onIndex={() => undefined}
      onClose={onClose}
      reviewSessionId={7}
      onSendFeedback={successfulHandoff}
    />)
    await userEvent.click(screen.getByRole('button', { name: 'Add feedback to chat' }))
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('does not treat arrow keys inside feedback fields as artifact navigation', async () => {
    const onIndex = vi.fn()
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[
        { type: 'image', title: 'Hero', path: 'artifacts/hero.png' },
        { type: 'image', title: 'Detail', path: 'artifacts/detail.png' },
      ]}
      index={0}
      onIndex={onIndex}
      onClose={() => undefined}
    />)

    const feedback = screen.getByRole('textbox', { name: 'General feedback' })
    await userEvent.click(feedback)
    await userEvent.keyboard('{ArrowLeft}{ArrowRight}')
    expect(onIndex).not.toHaveBeenCalled()
  })

  it('can hand off feedback for a stale artifact when its producing chat still exists', async () => {
    fsRead.mockRejectedValue(new Error('gone'))
    const onSendFeedback = vi.fn().mockResolvedValue({ ok: true })
    const onClose = vi.fn()
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'doc', title: 'Old report', path: 'reports/gone.md' }]}
      index={0}
      onIndex={() => undefined}
      onClose={onClose}
      reviewSessionId={7}
      onSendFeedback={onSendFeedback}
    />)

    expect(await screen.findByText('Could not read this file.')).toBeInTheDocument()
    await userEvent.type(screen.getByRole('textbox', { name: 'General feedback' }), 'Recreate this report.')
    await userEvent.click(screen.getByRole('button', { name: 'Add feedback to chat' }))

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(onSendFeedback).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: 7,
      text: expect.stringContaining('Recreate this report.'),
    }))
  })

  it('uses the artifact target for text and media resolution instead of its display path', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'brief.md',
    }
    fsRead.mockResolvedValue({ content: '# Ops brief' })
    const item = {
      type: 'doc',
      title: 'brief.md',
      path: 'brief.md',
      target,
    } as Parameters<typeof ArtifactViewer>[0]['items'][number]
    const { unmount } = render(<ArtifactViewer
      token="token"
      slug="master"
      items={[item]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(await screen.findByText('# Ops brief')).toBeInTheDocument()
    expect(fsRead).toHaveBeenCalledWith(target)
    unmount()

    const image = {
      ...item,
      type: 'image',
      title: 'visual.png',
      path: 'visual.png',
      target: { ...target, path: 'visual.png' },
    } as Parameters<typeof ArtifactViewer>[0]['items'][number]
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[image]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(previewUrl).toHaveBeenCalledWith('master', 'visual.png', image.target)
  })

  it('passes the originating Area and document path to Markdown resources', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'reports/brief.md',
    }
    fsRead.mockResolvedValue({ content: '![Chart](images/chart.png)' })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'doc', title: 'Brief', path: 'brief.md', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    const markdown = await screen.findByTestId('message-content')
    expect(markdown).toHaveAttribute('data-source-path', 'reports/brief.md')
    expect(JSON.parse(markdown.getAttribute('data-file-target') || '{}')).toEqual(target)
  })

  it('renders targeted HTML passive and script-free by default', () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'site/index.html',
    }
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Site', path: 'site/index.html', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(screen.getByTitle('index.html')).toHaveAttribute('sandbox', '')
    expect(screen.getByText('Passive preview')).toBeInTheDocument()
    expect(previewUrl).toHaveBeenCalledWith(
      'master',
      'site/index.html',
      target,
      undefined,
    )
  })

  it('keeps legacy HTML passive without offering unscoped active mode', () => {
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Legacy', path: 'legacy.html' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    const frame = screen
      .getAllByTitle('legacy.html')
      .find(node => node.tagName === 'IFRAME')
    expect(frame).toHaveAttribute('sandbox', '')
    expect(screen.queryByRole('button', { name: 'Enable active preview' })).not.toBeInTheDocument()
  })

  it('requires explicit trust consent and revokes active mode back to passive', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'site/index.html',
    }
    setTargetPreviewMode
      .mockResolvedValueOnce({ active: true, generation: 'g'.repeat(43) })
      .mockResolvedValueOnce({ active: false, generation: null })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Site', path: 'site/index.html', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    await userEvent.click(screen.getByRole('button', { name: 'Enable active preview' }))
    const consent = screen.getByRole('alertdialog', { name: 'Enable trusted active content?' })
    expect(consent).toHaveTextContent('run scripts and module workers')
    expect(consent).toHaveTextContent('send any data from this Area to external services')
    expect(consent).toHaveTextContent('cannot guarantee Area confidentiality')
    expect(setTargetPreviewMode).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Enable trusted active mode' }))
    await waitFor(() => expect(screen.getByText('Active preview')).toBeInTheDocument())
    expect(screen.getByTitle('index.html')).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin')
    expect(setTargetPreviewMode).toHaveBeenNthCalledWith(
      1,
      'token',
      'master',
      target,
      expect.stringMatching(/^[A-Za-z0-9_-]{32,128}$/),
      true,
    )
    expect(previewUrl).toHaveBeenLastCalledWith(
      'master',
      'site/index.html',
      target,
      expect.objectContaining({ generation: 'g'.repeat(43) }),
    )

    await userEvent.click(screen.getByRole('button', { name: 'Disable active preview' }))
    await waitFor(() => expect(screen.getByText('Passive preview')).toBeInTheDocument())
    expect(screen.getByTitle('index.html')).toHaveAttribute('sandbox', '')
    expect(setTargetPreviewMode).toHaveBeenNthCalledWith(
      2,
      'token',
      'master',
      target,
      expect.stringMatching(/^[A-Za-z0-9_-]{32,128}$/),
      false,
      'g'.repeat(43),
    )
  })

  it('shows an actionable fallback instead of loading forever for a directory or unknown binary', () => {
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'app', title: 'Starter app', path: 'artifacts/starter-app' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
    expect(screen.getByText(/Can't preview this file type/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Download' })).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          href: expect.stringContaining('/preview/artifacts/starter-app'),
        }),
      ]),
    )
    expect(fsRead).not.toHaveBeenCalled()
  })

  it('pins an annotation and returns actionable feedback to the producing chat', async () => {
    const onSendFeedback = vi.fn().mockResolvedValue({ ok: true })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'image', title: 'Hero', path: 'artifacts/hero.png' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
      reviewSessionId={7}
      onSendFeedback={onSendFeedback}
    />)

    await userEvent.click(screen.getByRole('button', { name: 'Annotate' }))
    const layer = screen.getByLabelText('Click to place an annotation')
    vi.spyOn(layer, 'getBoundingClientRect').mockReturnValue({
      x: 10, y: 20, left: 10, top: 20, right: 210, bottom: 120, width: 200, height: 100,
      toJSON: () => ({}),
    })
    fireEvent.click(layer, { clientX: 60, clientY: 90 })
    await userEvent.type(screen.getByLabelText('What should change here?'), 'Use the approved logo lockup.')
    await userEvent.click(screen.getByRole('button', { name: 'Add note' }))

    expect(screen.getByRole('button', { name: /Annotation 1: Use the approved logo/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Add feedback to chat' }))

    expect(onSendFeedback).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: 7,
      text: expect.stringContaining('Pin 1 (25% from left, 70% from top): Use the approved logo lockup.'),
    }))
  })

  it('opens a Mermaid block as an editable whiteboard and includes its saved path in feedback', async () => {
    fsRead.mockResolvedValue({ content: '# Flow\n\n```mermaid\ngraph LR\n A-->B\n```' })
    const onSendFeedback = vi.fn().mockResolvedValue({ ok: true })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'doc', title: 'Flow', path: 'reports/flow.md' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
      reviewSessionId={9}
      onSendFeedback={onSendFeedback}
    />)

    const edit = await screen.findByRole('button', { name: /Edit diagram graph LR/ })
    await userEvent.click(edit)
    expect(await screen.findByTestId('whiteboard')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save whiteboard' }))
    await userEvent.click(screen.getByRole('button', { name: 'Back to artifact' }))

    await waitFor(() => expect(screen.getByText('artifacts/whiteboards/flow.excalidraw')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Add feedback to chat' }))
    expect(onSendFeedback).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: 9,
      text: expect.stringContaining('[flow.excalidraw](artifacts/whiteboards/flow.excalidraw)'),
    }))
  })
})
