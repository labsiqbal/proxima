import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FolderLinker } from './FolderLinker'
import { browseDirs, linkProject } from '../../api/projects'
import type { Project } from '../../types'

vi.mock('../../api/projects', () => ({
  browseDirs: vi.fn(),
  linkProject: vi.fn(),
}))

const project: Project = {
  slug: 'fresh-app',
  name: 'Fresh App',
  path: '/home/user/code/fresh-app',
  owner: 'user',
  role: 'owner',
  visibility: 'private',
}

const dirs = {
  path: '/home/user/code',
  parent: '/home/user',
  dirs: [{ name: 'existing', path: '/home/user/code/existing' }],
  roots: ['/home/user'],
}

describe('FolderLinker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(browseDirs).mockResolvedValue(dirs)
    vi.mocked(linkProject).mockResolvedValue(project)
  })

  it('links the current folder in link mode', async () => {
    const user = userEvent.setup()
    const onLinked = vi.fn().mockResolvedValue(undefined)
    render(<FolderLinker token="tok" onLinked={onLinked} />)

    expect(await screen.findByText('/home/user/code')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Project display name' }))
      .toHaveAttribute('name', 'project-display-name')
    await user.click(screen.getByRole('button', { name: /Link “code”/ }))

    await waitFor(() => {
      expect(linkProject).toHaveBeenCalledWith('tok', { path: '/home/user/code', name: undefined })
    })
    expect(onLinked).toHaveBeenCalledWith(project)
  })

  it('creates a new folder under the browsed parent', async () => {
    const user = userEvent.setup()
    const onLinked = vi.fn().mockResolvedValue(undefined)
    render(<FolderLinker token="tok" onLinked={onLinked} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: /Create new folder/ }))
    expect(screen.getByPlaceholderText('my-project'))
      .toHaveAttribute('name', 'folder-name')
    await user.type(screen.getByPlaceholderText('my-project'), 'fresh-app')
    expect(screen.getByPlaceholderText('fresh-app'))
      .toHaveAttribute('name', 'project-display-name')
    await user.type(screen.getByPlaceholderText('fresh-app'), 'Fresh App')
    await user.click(screen.getByRole('button', { name: /Create “fresh-app” here/ }))

    await waitFor(() => {
      expect(linkProject).toHaveBeenCalledWith('tok', {
        path: '/home/user/code/fresh-app',
        name: 'Fresh App',
        mkdir: true,
      })
    })
    expect(onLinked).toHaveBeenCalledWith(project)
  })

  it('surfaces API failures as human-readable errors', async () => {
    const user = userEvent.setup()
    vi.mocked(linkProject).mockRejectedValue(new Error('a folder with that name already exists'))
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: /Create new folder/ }))
    await user.type(screen.getByPlaceholderText('my-project'), 'taken')
    await user.click(screen.getByRole('button', { name: /Create “taken” here/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/already exists/i)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByPlaceholderText('my-project')).toHaveFocus()
  })

  it('rejects slash-containing folder names client-side', async () => {
    const user = userEvent.setup()
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: /Create new folder/ }))
    const folderName = screen.getByPlaceholderText('my-project')
    await user.type(folderName, 'bad/name')
    await user.click(screen.getByRole('button', { name: /Create “bad\/name” here/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/cannot contain slashes/i)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(folderName).toHaveFocus()
    expect(folderName).toHaveAttribute('aria-invalid', 'true')
    expect(folderName).toHaveAttribute('aria-describedby', alert.id)
    expect(linkProject).not.toHaveBeenCalled()
  })

  it('uses pressed buttons with ordinary keyboard traversal for folder choice', async () => {
    const user = userEvent.setup()
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    const group = screen.getByRole('group', { name: 'Folder action' })
    const link = screen.getByRole('button', { name: 'Link existing' })
    const create = screen.getByRole('button', { name: 'Create new folder' })

    expect(group).toContainElement(link)
    expect(group).toContainElement(create)
    expect(link).toHaveAttribute('aria-pressed', 'true')
    expect(create).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()

    link.focus()
    await user.tab()
    expect(create).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(create).toHaveAttribute('aria-pressed', 'true')
    expect(link).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByPlaceholderText('my-project')).toBeInTheDocument()
  })
})
