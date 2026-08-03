import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { projectFs } from '../../api/fsAdapter'
import { DocumentEditor } from './DocumentEditor'

const read = vi.fn()
const write = vi.fn()

vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    list: vi.fn(async () => ({ entries: [] })),
    read: (...args: unknown[]) => read(...args),
    write: (...args: unknown[]) => write(...args),
    mkdir: vi.fn(),
    rename: vi.fn(),
    remove: vi.fn(),
  })),
}))

describe('DocumentEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    read.mockResolvedValue({ content: '# Plan\n' })
    write.mockResolvedValue({})
  })

  it('opens markdown in the wiki editor, editable immediately', async () => {
    render(<DocumentEditor token="t" slug="alpha" path="reports/plan.md" onClose={() => {}} />)

    // Edit mode from the first frame: no preview step between owner and editor.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toHaveClass('active'))
    expect(screen.getByRole('button', { name: 'Preview' })).not.toHaveClass('active')
    await waitFor(() => expect(document.querySelector('.cm-content')).toHaveTextContent('Plan'))
    expect(projectFs).toHaveBeenCalledWith('t', 'alpha')
  })

  it('opens other text documents in the file editor and saves them', async () => {
    const user = userEvent.setup()
    read.mockResolvedValue({ content: 'first line' })
    render(<DocumentEditor token="t" slug="alpha" path="notes/todo.txt" onClose={() => {}} />)

    expect(await screen.findByText('first line')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Preview' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(write).toHaveBeenCalledWith('notes/todo.txt', 'first line'))
  })

  // Layout-mapped Areas (#136/#138): the artifact carries the ref the Files API
  // needs, so the editor must read and write through it, not the bare path.
  it('reads an Area-mapped document through its target', async () => {
    const target = { project: 'alpha', area: { kind: 'ops' as const }, path: 'wiki/index.md' }
    render(<DocumentEditor token="t" slug="alpha" path="ops/wiki/index.md" target={target} onClose={() => {}} />)
    await waitFor(() => expect(read).toHaveBeenCalledWith(target))
  })

  it('names the way back where the file name lives', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<DocumentEditor token="t" slug="alpha" path="notes/todo.txt" backLabel="Gallery" onClose={onClose} />)
    const back = await screen.findByRole('button', { name: 'Back to gallery' })
    expect(back).toHaveTextContent('← Gallery')
    await user.click(back)
    expect(onClose).toHaveBeenCalled()
  })

  // #159: a way back out of a main-window surface leads its toolbar row, the
  // position every other back affordance in the app takes (chrome Back, the
  // artifact viewer's ← Gallery, the app viewport's). It is not a pane Close
  // trailing the actions, which is where both editors used to put it.
  it.each([
    ['markdown', 'reports/plan.md', '.wiki-note-head'],
    ['text', 'notes/todo.txt', '.file-editor-head'],
  ])('leads the %s editor toolbar with the way back', async (_kind, path, head) => {
    render(<DocumentEditor token="t" slug="alpha" path={path} backLabel="Gallery" onClose={() => {}} />)
    const bar = await waitFor(() => {
      const found = document.querySelector(head)
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    const controls = within(bar).getAllByRole('button')
    expect(controls[0]).toHaveAccessibleName('Back to gallery')
    expect(within(bar).getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })
})
