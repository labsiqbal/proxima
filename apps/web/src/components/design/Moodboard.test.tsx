import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  addMoodboardItem,
  listMoodboard,
  updateMoodboardItem,
  uploadFile,
  type MoodboardItem,
} from '../../api/files'
import { Moodboard } from './Moodboard'

vi.mock('../../api/files', () => ({
  listMoodboard: vi.fn(),
  addMoodboardItem: vi.fn(),
  updateMoodboardItem: vi.fn(),
  deleteMoodboardItem: vi.fn().mockResolvedValue({ ok: true, id: 'one' }),
  uploadFile: vi.fn(),
  fileUrl: (_slug: string, path: string) => `/preview/${path}`,
}))
vi.mock('../ui/Dialog', () => ({
  confirmDialog: vi.fn().mockResolvedValue(true),
}))

const items: MoodboardItem[] = [{
  id: 'one',
  kind: 'link',
  url: 'https://linear.app/',
  imagePath: 'artifacts/moodboard/images/one.png',
  title: 'Linear hero',
  siteName: 'linear.app',
  faviconUrl: null,
  note: 'Quiet, spacious hero',
  tags: ['hero', 'dark'],
  useAsReference: false,
  createdAt: '2026-07-26T00:00:00Z',
  updatedAt: '2026-07-26T00:00:00Z',
}, {
  id: 'two',
  kind: 'upload',
  url: null,
  imagePath: 'artifacts/moodboard/images/two.png',
  title: 'Pricing study',
  siteName: 'Uploaded screenshot',
  faviconUrl: null,
  note: '',
  tags: ['pricing'],
  useAsReference: false,
  createdAt: '2026-07-26T00:00:00Z',
  updatedAt: '2026-07-26T00:00:00Z',
}]

describe('Moodboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listMoodboard).mockResolvedValue({ items: structuredClone(items) })
  })

  it('renders, filters, edits, and selects a card for design runs', async () => {
    const user = userEvent.setup()
    vi.mocked(updateMoodboardItem).mockImplementation(async (_token, _slug, id, body) => ({
      item: {
        ...(items.find(item => item.id === id) as MoodboardItem),
        ...body,
        updatedAt: 'later',
      },
    }))
    render(<Moodboard token="token" slug="demo" />)

    expect(await screen.findByText('Linear hero')).toBeInTheDocument()
    expect(screen.getByText('Pricing study')).toBeInTheDocument()

    await user.click(within(screen.getByLabelText('Filter by tag')).getByRole('button', { name: '#pricing' }))
    expect(screen.queryByText('Linear hero')).not.toBeInTheDocument()
    expect(screen.getByText('Pricing study')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Showing #pricing/i }))

    await user.type(screen.getByRole('searchbox', { name: 'Search references' }), 'quiet')
    expect(screen.getByText('Linear hero')).toBeInTheDocument()
    expect(screen.queryByText('Pricing study')).not.toBeInTheDocument()
    await user.clear(screen.getByRole('searchbox', { name: 'Search references' }))

    await user.click(screen.getByRole('button', { name: 'Use linear.app as reference' }))
    await waitFor(() => expect(updateMoodboardItem).toHaveBeenCalledWith(
      'token',
      'demo',
      'one',
      { useAsReference: true },
    ))
    expect(await screen.findByText('Used in design')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Edit linear.app' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit reference' })
    await user.clear(within(dialog).getByLabelText('Note'))
    await user.type(within(dialog).getByLabelText('Note'), 'Borrow the type rhythm')
    await user.clear(within(dialog).getByLabelText('Tags'))
    await user.type(within(dialog).getByLabelText('Tags'), 'type, editorial')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateMoodboardItem).toHaveBeenLastCalledWith(
      'token',
      'demo',
      'one',
      { note: 'Borrow the type rhythm', tags: ['type', 'editorial'] },
    ))
  })

  it('adds a pasted link and an uploaded screenshot', async () => {
    const user = userEvent.setup()
    const linkItem = { ...items[0], id: 'link-new', siteName: 'new.test', title: 'New preview' }
    const uploadItem = { ...items[1], id: 'upload-new', title: 'screen', siteName: 'Uploaded screenshot' }
    vi.mocked(addMoodboardItem)
      .mockResolvedValueOnce({ item: linkItem, warning: null })
      .mockResolvedValueOnce({ item: uploadItem, warning: null })
    vi.mocked(uploadFile).mockResolvedValue({
      path: 'artifacts/moodboard/images/screen.png',
      name: 'screen.png',
    })
    render(<Moodboard token="token" slug="demo" />)
    await screen.findByText('Linear hero')

    await user.click(screen.getByRole('button', { name: '+ Add' }))
    const linkInput = screen.getByRole('textbox', { name: 'Reference URL' })
    fireEvent.paste(linkInput, { clipboardData: { getData: () => 'https://new.test/' } })
    await waitFor(() => expect(addMoodboardItem).toHaveBeenCalledWith(
      'token',
      'demo',
      { url: 'https://new.test/' },
    ))
    expect(await screen.findByText('New preview')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '+ Add' }))
    const upload = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['image'], 'screen.png', { type: 'image/png' })
    fireEvent.change(upload, { target: { files: [file] } })
    await waitFor(() => expect(uploadFile).toHaveBeenCalledWith(
      'token',
      'demo',
      file,
      'artifacts/moodboard/images',
    ))
    await waitFor(() => expect(addMoodboardItem).toHaveBeenLastCalledWith(
      'token',
      'demo',
      {
        imagePath: 'artifacts/moodboard/images/screen.png',
        title: 'screen',
        siteName: 'Uploaded screenshot',
      },
    ))
    expect(await screen.findByText('screen')).toBeInTheDocument()
  })
})
