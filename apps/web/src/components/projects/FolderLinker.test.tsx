import '@testing-library/jest-dom/vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FolderLinker } from './FolderLinker'
import { apiErrorDetail, browseDirs, linkProject, linkProjectErrorField } from '../../api/projects'
import { ApiError } from '../../api/client'
import type { Project } from '../../types'

vi.mock('../../api/projects', async importOriginal => {
  const actual = await importOriginal<typeof import('../../api/projects')>()
  return {
    ...actual,
    browseDirs: vi.fn(),
    linkProject: vi.fn(),
  }
})

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
  root_id: 'root-owner',
}

describe('FolderLinker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(browseDirs).mockResolvedValue(dirs)
    vi.mocked(linkProject).mockResolvedValue(project)
  })

  it('renders a focused retry target when initial browsing fails', async () => {
    const user = userEvent.setup()
    const loadError = new ApiError(
      403,
      'GET /api/fs/dirs?path= failed (403): No readable folder is available inside the allowed roots',
      '/api/fs/dirs?path=',
      'GET',
      'path',
      'No readable folder is available inside the allowed roots',
    )
    vi.mocked(browseDirs)
      .mockRejectedValueOnce(loadError)
      .mockResolvedValueOnce(dirs)
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    const alert = await screen.findByRole('alert')
    const retry = screen.getByRole('button', {
      name: 'Folder browser. Retry folders',
    })
    expect(alert).toHaveTextContent('No readable folder is available inside the allowed roots')
    expect(alert).not.toHaveTextContent('/api/fs/dirs')
    expect(alert).toHaveTextContent(apiErrorDetail(loadError))
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(retry).toHaveFocus()
    expect(retry).toHaveAttribute('aria-invalid', 'true')
    expect(retry).not.toHaveAttribute('aria-describedby')
    expect(linkProjectErrorField(loadError)).toBe('path')

    await user.keyboard('{Enter}')

    const selected = await screen.findByRole('button', {
      name: /Selected folder: \/home\/user\/code\. Refresh folders/,
    })
    expect(selected).toHaveFocus()
    expect(selected).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(browseDirs).toHaveBeenLastCalledWith('tok', '', '')
  })

  it('announces only the structured detail when refresh browsing fails', async () => {
    const user = userEvent.setup()
    const refreshError = new ApiError(
      403,
      'GET /api/fs/dirs?path=%2Fhome%2Fuser%2Fcode failed (403): No readable folder is available inside the allowed roots',
      '/api/fs/dirs?path=%2Fhome%2Fuser%2Fcode',
      'GET',
      'path',
      'No readable folder is available inside the allowed roots',
    )
    vi.mocked(browseDirs)
      .mockResolvedValueOnce(dirs)
      .mockRejectedValueOnce(refreshError)
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    const selected = await screen.findByRole('button', {
      name: /Selected folder: \/home\/user\/code\. Refresh folders/,
    })
    await user.click(selected)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('No readable folder is available inside the allowed roots')
    expect(alert).not.toHaveTextContent('/api/fs/dirs')
    expect(alert).toHaveTextContent(apiErrorDetail(refreshError))
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(selected).toHaveFocus()
    expect(selected).toHaveAttribute('aria-invalid', 'true')
    expect(linkProjectErrorField(refreshError)).toBe('path')
  })

  it('announces only the structured detail for no-readable-ancestor recovery failures', async () => {
    const user = userEvent.setup()
    const ancestorError = new ApiError(
      403,
      'GET /api/fs/dirs?path=%2Fmissing failed (403): No readable folder is available inside the allowed roots',
      '/api/fs/dirs?path=%2Fmissing',
      'GET',
      'path',
      'No readable folder is available inside the allowed roots',
    )
    vi.mocked(browseDirs)
      .mockResolvedValueOnce(dirs)
      .mockRejectedValueOnce(ancestorError)
      .mockResolvedValueOnce(dirs)
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: '↑ ..' }))

    const alert = await screen.findByRole('alert')
    const selected = screen.getByRole('button', {
      name: /Selected folder: \/home\/user\/code\. Refresh folders/,
    })
    expect(alert).toHaveTextContent('No readable folder is available inside the allowed roots')
    expect(alert).not.toHaveTextContent('GET /api/fs/dirs')
    expect(alert).not.toHaveTextContent('failed (403)')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(selected).toHaveFocus()
    expect(selected).toHaveAttribute('aria-invalid', 'true')

    await user.click(selected)
    const recovered = await screen.findByRole('button', {
      name: /Selected folder: \/home\/user\/code\. Refresh folders/,
    })
    expect(recovered).toHaveFocus()
    expect(recovered).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
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
      expect(linkProject).toHaveBeenCalledWith('tok', {
        path: '/home/user/code',
        root_id: 'root-owner',
        name: undefined,
      })
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
        root_id: 'root-owner',
        name: 'Fresh App',
        mkdir: true,
      })
    })
    expect(onLinked).toHaveBeenCalledWith(project)
  })

  it('surfaces API failures as human-readable errors', async () => {
    const user = userEvent.setup()
    const error = new ApiError(
      409,
      'POST /api/projects/link failed (409): a folder with that name already exists',
      '/api/projects/link',
      'POST',
      'folder',
      'a folder with that name already exists',
    )
    vi.mocked(linkProject).mockRejectedValue(error)
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: /Create new folder/ }))
    await user.type(screen.getByPlaceholderText('my-project'), 'taken')
    await user.click(screen.getByRole('button', { name: /Create “taken” here/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/already exists/i)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByPlaceholderText('my-project')).toHaveFocus()
    expect(linkProjectErrorField(error)).toBe('folder')
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
    expect(folderName).not.toHaveAttribute('aria-describedby')
    expect(linkProject).not.toHaveBeenCalled()

    act(() => screen.getByRole('button', { name: /Create “bad\/name” here/ }).click())
    const repeatedAlert = screen.getByRole('alert')
    expect(repeatedAlert).not.toBe(alert)
    expect(repeatedAlert).toHaveTextContent(/cannot contain slashes/i)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(folderName).toHaveFocus()
    expect(folderName).not.toHaveAttribute('aria-describedby')
  })

  it('returns an overlong display name to its corrective field', async () => {
    const user = userEvent.setup()
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: /Create new folder/ }))
    await user.type(screen.getByPlaceholderText('my-project'), 'valid-folder')
    const displayName = screen.getByPlaceholderText('valid-folder')
    await user.type(displayName, 'x'.repeat(121))
    await user.click(screen.getByRole('button', { name: /Create “valid-folder” here/ }))

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/120 characters or fewer/i)
    expect(displayName).toHaveFocus()
    expect(displayName).toHaveAttribute('aria-invalid', 'true')
    expect(displayName).not.toHaveAttribute('aria-describedby')
    expect(screen.getByPlaceholderText('my-project')).not.toHaveAttribute('aria-invalid')
    expect(linkProject).not.toHaveBeenCalled()
  })

  it('returns a structured slug collision to the display-name field', async () => {
    const user = userEvent.setup()
    const error = new ApiError(
      409,
      "POST /api/projects/link failed (409): slug 'shared-name' already exists - pick another",
      '/api/projects/link',
      'POST',
      'name',
      "A project with that display name already exists - choose another display name",
    )
    vi.mocked(linkProject).mockRejectedValue(error)
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: /Create new folder/ }))
    const folderName = screen.getByPlaceholderText('my-project')
    await user.type(folderName, 'different-folder')
    const displayName = screen.getByPlaceholderText('different-folder')
    await user.type(displayName, 'Shared Name')
    await user.click(screen.getByRole('button', { name: /Create “different-folder” here/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('A project with that display name already exists - choose another display name')
    expect(alert).not.toHaveTextContent('/api/projects/link')
    expect(displayName).toHaveFocus()
    expect(displayName).toHaveAttribute('aria-invalid', 'true')
    expect(displayName).not.toHaveAttribute('aria-describedby')
    expect(folderName).not.toHaveAttribute('aria-invalid')
    expect(linkProjectErrorField(error)).toBe('name')
  })

  it('returns a missing selected folder to its refresh control', async () => {
    const user = userEvent.setup()
    const selected = {
      path: '/home/user/code/existing',
      parent: '/home/user/code',
      dirs: [],
      roots: ['/home/user'],
      root_id: 'root-owner',
    }
    const missingPath = new ApiError(
      400,
      'POST /api/projects/link failed (400): not a directory',
      '/api/projects/link',
      'POST',
      'path',
      'not a directory',
    )
    vi.mocked(browseDirs)
      .mockResolvedValueOnce(dirs)
      .mockResolvedValueOnce(selected)
      .mockResolvedValueOnce(dirs)
    vi.mocked(linkProject).mockRejectedValue(missingPath)
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: 'existing' }))
    const selectedFolder = await screen.findByRole('button', {
      name: /Selected folder: \/home\/user\/code\/existing\. Refresh folders/,
    })
    await user.click(screen.getByRole('button', { name: /Link “existing”/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('not a directory')
    expect(selectedFolder).toHaveFocus()
    expect(selectedFolder).toHaveAttribute('aria-invalid', 'true')
    expect(selectedFolder).not.toHaveAttribute('aria-describedby')
    expect(screen.getByRole('textbox', { name: 'Project display name' }))
      .not.toHaveAttribute('aria-invalid')

    await user.click(selectedFolder)
    const recoveredFolder = await screen.findByRole('button', {
      name: /Selected folder: \/home\/user\/code\. Refresh folders/,
    })
    expect(recoveredFolder).toHaveFocus()
    expect(recoveredFolder).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(browseDirs).toHaveBeenLastCalledWith(
      'tok',
      '/home/user/code/existing',
      'root-owner',
    )
  })

  it('returns a missing create parent to the selected-folder control', async () => {
    const user = userEvent.setup()
    const missingParent = new ApiError(
      400,
      'POST /api/projects/link failed (400): parent directory does not exist',
      '/api/projects/link',
      'POST',
      'parent',
      'parent directory does not exist',
    )
    vi.mocked(linkProject).mockRejectedValue(missingParent)
    render(<FolderLinker token="tok" onLinked={vi.fn()} />)

    await screen.findByText('/home/user/code')
    await user.click(screen.getByRole('button', { name: /Create new folder/ }))
    const folderName = screen.getByPlaceholderText('my-project')
    await user.type(folderName, 'valid-child')
    await user.click(screen.getByRole('button', { name: /Create “valid-child” here/ }))

    const selectedFolder = screen.getByRole('button', {
      name: /Selected folder: \/home\/user\/code\. Refresh folders/,
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('parent directory does not exist')
    expect(selectedFolder).toHaveFocus()
    expect(selectedFolder).toHaveAttribute('aria-invalid', 'true')
    expect(folderName).not.toHaveAttribute('aria-invalid')
    expect(linkProjectErrorField(missingParent)).toBe('path')
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
