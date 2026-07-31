import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { FileEditor } from './FileEditor'
import type { ReadOnlyFsAdapter } from '../../api/fsAdapter'
import { confirmDialog } from '../ui/Dialog'

vi.mock('@uiw/react-codemirror', () => ({
  default: ({
    value,
    editable,
    onChange,
  }: {
    value?: string
    editable?: boolean
    onChange?: (value: string) => void
  }) => (
    <textarea
      data-testid="codemirror-stub"
      data-editable={String(editable !== false)}
      value={value ?? ''}
      readOnly={editable === false}
      onChange={e => onChange?.(e.target.value)}
    />
  ),
}))

vi.mock('../ui/Dialog', () => ({
  confirmDialog: vi.fn(async () => true),
}))

function mockFs(content: string, read = vi.fn(async () => ({ content }))): ReadOnlyFsAdapter & { read: ReturnType<typeof vi.fn> } {
  return { list: vi.fn(async () => ({ entries: [] })), read }
}

describe('FileEditor fs adapter swaps', () => {
  it('preserves unsaved edits across recovery inspection and ordinary Files restoration', async () => {
    const user = userEvent.setup()
    const projectRead = vi.fn(async () => ({ content: 'project bytes' }))
    const inspectionRead = vi.fn(async () => ({ content: 'inspection bytes' }))
    const write = vi.fn(async () => ({}))
    const projectFs = mockFs('project bytes', projectRead)
    const inspectionFs = mockFs('inspection bytes', inspectionRead)

    const view = render(
      <FileEditor fs={projectFs} write={write} path="notes/todo.md" onClose={() => {}} />,
    )

    await screen.findByDisplayValue('project bytes')
    const editor = screen.getByTestId('codemirror-stub')
    await user.clear(editor)
    await user.type(editor, 'unsaved owner edits')
    expect(screen.getByText(/Unsaved/)).toBeVisible()
    expect(screen.getByTitle('notes/todo.md')).toHaveTextContent('•')

    // Recovery reveal swaps to container inspection (read-only, same path).
    view.rerender(
      <FileEditor fs={inspectionFs} path="notes/todo.md" onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Unsaved · read-only during inspection')
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByTitle('notes/todo.md')).toHaveTextContent('•')
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(inspectionRead).not.toHaveBeenCalled()
    expect(projectRead).toHaveBeenCalledTimes(1)

    // Closing inspection restores ordinary project Files write access.
    view.rerender(
      <FileEditor fs={projectFs} write={write} path="notes/todo.md" onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByText(/Unsaved/)).toBeVisible()
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save' })).toBeVisible()
    expect(screen.getByTestId('codemirror-stub')).toHaveAttribute('data-editable', 'true')
    expect(projectRead).toHaveBeenCalledTimes(1)
    expect(inspectionRead).not.toHaveBeenCalled()
  })

  it('reloads when the path changes even if the previous buffer was dirty', async () => {
    const user = userEvent.setup()
    const read = vi.fn(async (path: string) => ({
      content: path === 'a.md' ? 'file a' : 'file b',
    }))
    const fs = mockFs('file a', read)
    const write = vi.fn(async () => ({}))

    const view = render(
      <FileEditor fs={fs} write={write} path="a.md" onClose={() => {}} />,
    )
    await screen.findByDisplayValue('file a')
    await user.type(screen.getByTestId('codemirror-stub'), ' dirty')

    view.rerender(
      <FileEditor fs={fs} write={write} path="b.md" onClose={() => {}} />,
    )

    await screen.findByDisplayValue('file b')
    expect(screen.queryByDisplayValue(/dirty/)).not.toBeInTheDocument()
    expect(read).toHaveBeenCalledWith('b.md')
  })

  it('reloads a clean buffer when only the fs adapter changes', async () => {
    const projectRead = vi.fn(async () => ({ content: 'project bytes' }))
    const inspectionRead = vi.fn(async () => ({ content: 'inspection bytes' }))
    const write = vi.fn(async () => ({}))

    const view = render(
      <FileEditor
        fs={mockFs('project bytes', projectRead)}
        write={write}
        path="notes/todo.md"
        onClose={() => {}}
      />,
    )
    await screen.findByDisplayValue('project bytes')

    view.rerender(
      <FileEditor
        fs={mockFs('inspection bytes', inspectionRead)}
        path="notes/todo.md"
        onClose={() => {}}
      />,
    )

    await screen.findByDisplayValue('inspection bytes')
    expect(inspectionRead).toHaveBeenCalledWith('notes/todo.md')
    expect(screen.getByRole('status')).toHaveTextContent('Read-only inspection')
  })

  it('keeps a dirty buffer when inspection browses a different path without write', async () => {
    const user = userEvent.setup()
    const projectRead = vi.fn(async () => ({ content: 'project bytes' }))
    const inspectionRead = vi.fn(async () => ({ content: 'other inspection file' }))
    const write = vi.fn(async () => ({}))

    const view = render(
      <FileEditor fs={mockFs('project bytes', projectRead)} write={write} path="notes/todo.md" onClose={() => {}} />,
    )
    await screen.findByDisplayValue('project bytes')
    await user.clear(screen.getByTestId('codemirror-stub'))
    await user.type(screen.getByTestId('codemirror-stub'), 'sticky unsaved')

    view.rerender(
      <FileEditor fs={mockFs('other inspection file', inspectionRead)} path="ops/other.md" onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Unsaved · read-only during inspection')
    })
    expect(screen.getByDisplayValue('sticky unsaved')).toBeVisible()
    expect(screen.getByTitle('notes/todo.md')).toHaveTextContent('•')
    expect(view.container.querySelector('.file-editor')).toHaveClass('file-editor-retained')
    expect(screen.getByText(/inspection tree stays available/i)).toBeVisible()
    expect(inspectionRead).not.toHaveBeenCalled()
  })

  it('confirms before Close discards unsaved edits', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const confirm = vi.mocked(confirmDialog)
    confirm.mockResolvedValueOnce(false)

    render(
      <FileEditor
        fs={mockFs('project bytes')}
        write={vi.fn(async () => ({}))}
        path="notes/todo.md"
        onClose={onClose}
      />,
    )
    await screen.findByDisplayValue('project bytes')
    await user.type(screen.getByTestId('codemirror-stub'), ' x')

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Discard unsaved edits?',
      confirmLabel: 'Discard',
      danger: true,
    }))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue('project bytes x')).toBeVisible()

    confirm.mockResolvedValueOnce(true)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
