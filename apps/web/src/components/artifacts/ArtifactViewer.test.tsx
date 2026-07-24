import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { ArtifactViewer } from './ArtifactViewer'

const fsRead = vi.fn()

vi.mock('../../api/files', () => ({
  previewUrl: vi.fn((_slug: string, path: string) => `/preview/${path}`),
}))
vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({ read: (...args: unknown[]) => fsRead(...args) })),
}))
vi.mock('../chat/MessageContent', () => ({
  MessageContent: ({ content }: { content: string }) => <div>{content}</div>,
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
  window.localStorage.clear()
})

describe('ArtifactViewer v2 review flow', () => {
  it('pins an annotation and returns actionable feedback to the producing chat', async () => {
    const onSendFeedback = vi.fn()
    render(<ArtifactViewer
      token="token"
      slug="alpha"
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
    const onSendFeedback = vi.fn()
    render(<ArtifactViewer
      token="token"
      slug="alpha"
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
