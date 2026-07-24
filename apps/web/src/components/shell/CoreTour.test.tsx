import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CoreTour } from './CoreTour'
import { getAlphaSettings, saveAlphaSettings } from '../../api/alpha'

vi.mock('../../api/alpha', () => ({ getAlphaSettings: vi.fn(), saveAlphaSettings: vi.fn() }))

describe('CoreTour', () => {
  beforeEach(() => {
    vi.mocked(getAlphaSettings).mockResolvedValue({ tour_core_done: false } as never)
    vi.mocked(saveAlphaSettings).mockResolvedValue({ tour_core_done: true } as never)
  })

  it('traps keyboard focus and advances the core chapters', async () => {
    const user = userEvent.setup()
    render(<><button type="button">Behind tour</button><CoreTour token="token" /></>)
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
})
