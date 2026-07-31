import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MasterEmpty } from './MasterScreen'
import { useMasterState } from '../master/MasterStateProvider'

const seedDraft = vi.fn()
vi.mock('../master/MasterStateProvider', () => ({ useMasterState: vi.fn() }))

describe('Master empty surface', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useMasterState).mockReturnValue({
      actions: { seedDraft },
    } as never)
  })

  it('renders a compact conversation-first empty state', () => {
    render(<MasterEmpty />)
    expect(
      screen.getByRole('heading', { name: 'What should Master take care of?' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Talk through an outcome/)).toBeInTheDocument()
    expect(screen.queryByLabelText('What you can do here')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Getting started')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'How it works' })).toBeInTheDocument()
    const examples = screen.getByLabelText('Example messages')
    expect(within(examples).getByRole('button', { name: 'Audit and fix' })).toHaveClass(
      'master-example-chip',
    )
    expect(within(examples).getByRole('button', { name: 'Split the release' })).toBeInTheDocument()
    expect(within(examples).getByRole('button', { name: 'Review the fleet' })).toBeInTheDocument()
  })

  it('seeds the shared provider draft from an example', async () => {
    render(<MasterEmpty />)
    const chip = screen.getByRole('button', { name: 'Audit and fix' })
    expect(chip).toHaveAttribute(
      'title',
      'Audit this Project and delegate independent fixes.',
    )
    await userEvent.setup().click(chip)
    expect(seedDraft).toHaveBeenCalledWith(
      'Audit this Project and delegate independent fixes.',
    )
  })

  it('opens and dismisses the accessible help dialog', async () => {
    const user = userEvent.setup()
    render(<MasterEmpty />)
    const trigger = screen.getByRole('button', { name: 'How it works' })
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'How Master works' })
    expect(within(dialog).getByLabelText('What you can do here')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Getting started')).toBeInTheDocument()
    expect(within(dialog).getByText(/Describe the outcome/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Got it' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
    await user.click(trigger)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(trigger)
    fireEvent.click(screen.getByTestId('master-empty-help-scrim'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
