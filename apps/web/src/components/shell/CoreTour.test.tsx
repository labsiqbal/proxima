import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CoreTour } from './CoreTour'
import { getMasterSettings, saveMasterSettings } from '../../api/master'

vi.mock('../../api/master', () => ({ getMasterSettings: vi.fn(), saveMasterSettings: vi.fn() }))

describe('CoreTour', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(getMasterSettings).mockResolvedValue({ tour_core_done: false } as never)
    vi.mocked(saveMasterSettings).mockResolvedValue({ tour_core_done: true } as never)
  })

  it('traps keyboard focus and advances the core chapters', async () => {
    const user = userEvent.setup()
    render(<><button type="button">Behind tour</button><CoreTour token="token" masterEnabled /></>)
    const dialog = await screen.findByRole('dialog', { name: 'Welcome to Proxima' })
    await waitFor(() => expect(dialog).toHaveFocus())

    await user.tab()
    expect(screen.getByRole('button', { name: 'Skip tour' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Next' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Skip tour' })).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Behind tour' })).not.toHaveFocus()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByRole('heading', { name: 'Chat keeps you close' })).toBeInTheDocument()
  })

  it('keeps the general tour but removes the Master chapter while disabled', async () => {
    const user = userEvent.setup()
    render(<CoreTour token="token" masterEnabled={false} />)
    expect(await screen.findByRole('dialog', { name: 'Welcome to Proxima' })).toBeInTheDocument()
    expect(getMasterSettings).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByRole('heading', { name: 'Chat keeps you close' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByRole('heading', { name: 'Tasks and Workflows' })).toBeInTheDocument()
    expect(screen.queryByText('Master is the side path')).not.toBeInTheDocument()
  })
})
