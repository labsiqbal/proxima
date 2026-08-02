import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { containerInspectionFs, projectFs } from '../api/fsAdapter'
import { FilesScreen } from './FilesScreen'
import type { Project } from '../types'

vi.mock('../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({
      entries: [{ name: 'note.md', type: 'file', size: 12 }],
    }),
    read: vi.fn().mockResolvedValue({ content: '# note' }),
    write: vi.fn(),
    mkdir: vi.fn(),
    rename: vi.fn(),
    remove: vi.fn(),
  })),
  containerInspectionFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({ entries: [] }),
    read: vi.fn(),
  })),
}))
vi.mock('../components/artifacts/ArtifactViewer', () => ({
  ArtifactViewer: ({ slug, items }: { slug: string; items: { path: string }[] }) => (
    <div data-testid="viewer">viewer:{slug}:{items[0]?.path}</div>
  ),
}))

const alpha = { slug: 'alpha', name: 'Alpha', visibility: 'private' } as Project
const beta = { slug: 'beta', name: 'Beta', visibility: 'private' } as Project

describe('FilesScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('browses only the active Container in Work', async () => {
    render(<FilesScreen token="t" projects={[alpha, beta]} activeProject={alpha} />)
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(projectFs).not.toHaveBeenCalledWith('t', 'beta')
    // No per-project headings: one tree, scoped by the sidebar's project switcher.
    expect(screen.queryByRole('heading', { name: 'Alpha', level: 2 })).not.toBeInTheDocument()
  })

  it('goes global in Delegate and narrows through the head filter', async () => {
    const user = userEvent.setup()
    render(<FilesScreen token="t" projects={[alpha, beta]} globalScope />)
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(projectFs).toHaveBeenCalledWith('t', 'beta')
    expect(screen.getByRole('heading', { name: 'Alpha', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Beta', level: 2 })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /All projects/ }))
    await user.click(await screen.findByRole('option', { name: 'Beta' }))
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Alpha', level: 2 })).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Beta', level: 2 })).toBeInTheDocument()
  })

  it('opens a picked file in the ArtifactViewer', async () => {
    const user = userEvent.setup()
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /note\.md/ }))
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:note.md')
  })

  it('uses the read-only Container adapter for migration reveal targets', async () => {
    render(
      <FilesScreen
        token="t"
        projects={[alpha]}
        activeProject={alpha}
        revealPath={{ slug: 'alpha', path: 'wiki', pathKind: 'directory', rootSide: 'container' }}
      />,
    )
    await waitFor(() => expect(containerInspectionFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(projectFs).not.toHaveBeenCalled()
  })

  it('drops back to the ordinary adapter once the reveal is gone', async () => {
    const { rerender } = render(
      <FilesScreen
        token="t"
        projects={[alpha]}
        activeProject={alpha}
        revealPath={{ slug: 'alpha', path: 'wiki', pathKind: 'directory', rootSide: 'container' }}
      />,
    )
    await waitFor(() => expect(containerInspectionFs).toHaveBeenCalled())
    rerender(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} revealPath={null} />)
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'alpha'))
  })
})
