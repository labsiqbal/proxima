import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AlphaScreen } from './AlphaScreen'
import { getAlphaDesk, sendAlphaMessage } from '../api/alpha'
import { listMessages } from '../api/sessions'

vi.mock('../api/alpha', () => ({
  getAlphaDesk: vi.fn(), sendAlphaMessage: vi.fn(), saveAlphaSettings: vi.fn(),
  previewCheckpointRestore: vi.fn(), restoreCheckpoint: vi.fn(), setCheckpointPinned: vi.fn(),
}))
vi.mock('../api/sessions', () => ({ listMessages: vi.fn() }))

const desk = {
  session: { id: 9, title: 'Alpha', mode: 'alpha' },
  alpha_run: null,
  backing_runner: 'pi',
  jobs: [], unattended: false,
  budgets: { unattended: false, budget_turns: 20, budget_wall_seconds: 14400, budget_tokens: null, tour_core_done: true },
  capacity: { running: 0, max: 3, free: 3, queued: 0 },
  attention: [], checkpoints: [],
}
const runners = [{ id: 'pi', displayName: 'Pi', installed: true, runnable: true }]

describe('AlphaScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getAlphaDesk).mockResolvedValue(desk as never)
    vi.mocked(listMessages).mockResolvedValue({ messages: [], goal: null })
    vi.mocked(sendAlphaMessage).mockResolvedValue({ run_id: 1, session_id: 9, status: 'queued' })
  })

  it('renders the honest empty, capacity, safety, and delegation states', async () => {
    render(<AlphaScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)

    // Header matches Chat/code-header: eyebrow + strong, not a marketing h1.
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Orchestration')).toBeInTheDocument()
    expect(screen.getByText('0 running / 3 free')).toBeInTheDocument()
    expect(screen.getByText('No delegated work')).toBeInTheDocument()
    expect(screen.getByText('Nothing is blocked')).toBeInTheDocument()
    expect(screen.getByText('No checkpoints yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unattended off' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('combobox', { name: 'Backing runner' })).toBeInTheDocument()
  })

  it('fills an example and guards the async delegation submit', async () => {
    const user = userEvent.setup()
    render(<AlphaScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Audit this project and delegate independent fixes.' }))
    await user.click(screen.getByRole('button', { name: 'Delegate' }))

    expect(sendAlphaMessage).toHaveBeenCalledWith('token', 'Audit this project and delegate independent fixes.')
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Delegate an outcome' })).toHaveValue(''))
  })
})
