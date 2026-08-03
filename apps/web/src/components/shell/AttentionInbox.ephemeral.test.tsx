import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AttentionInbox } from './AttentionInbox'
import { getAttention } from '../../api/master'
import { dismissAttention } from '../../api/inbox'

vi.mock('../../api/master', () => ({
  getAttention: vi.fn(),
  actAttention: vi.fn(),
  getMasterDecision: vi.fn(),
  deferMasterDecision: vi.fn(),
  resolveMasterDecision: vi.fn(),
}))
vi.mock('../../api/inbox', () => ({ dismissAttention: vi.fn() }))

const budget = {
  id: 'attention:3', kind: 'master_budget', title: 'Master unattended work stopped',
  target: { view: 'master', section: 'budgets' }, inline_ok: false, actions: [],
  status: 'open', created_at: '2026-01-01 09:00:00',
  severity: 'warning', body: 'Master stopped its unattended queue because its turn budget was reached.',
  requires_action: true, read: false,
}

describe('Header notifications are ephemeral (#157/#158)', () => {
  beforeEach(() => {
    vi.mocked(getAttention).mockReset().mockResolvedValue({ items: [budget], count: 1 } as never)
    vi.mocked(dismissAttention).mockReset().mockResolvedValue({ ok: true, id: 'attention:3' })
  })

  it('badges the unread count the server reports', async () => {
    vi.mocked(getAttention).mockResolvedValue({ items: [budget], count: 4 } as never)
    render(<AttentionInbox token="t" onOpenTarget={vi.fn()} onOpenInbox={vi.fn()} />)

    expect(await screen.findByRole('button', { name: /4 unread notifications/ })).toBeInTheDocument()
  })

  it('handles a notification on click: it opens and leaves the header', async () => {
    const user = userEvent.setup()
    const onOpenTarget = vi.fn()
    render(<AttentionInbox token="t" onOpenTarget={onOpenTarget} onOpenInbox={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /1 unread notification/ }))

    await user.click(screen.getByRole('button', { name: /Master unattended work stopped/ }))

    expect(dismissAttention).toHaveBeenCalledWith('t', 'attention:3')
    expect(onOpenTarget).toHaveBeenCalledWith(budget.target)
  })

  it('dismisses a navigate-only item without opening it (#157)', async () => {
    const user = userEvent.setup()
    const onOpenTarget = vi.fn()
    render(<AttentionInbox token="t" onOpenTarget={onOpenTarget} onOpenInbox={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /1 unread notification/ }))

    await user.click(screen.getByRole('button', { name: 'Dismiss' }))

    await waitFor(() => expect(dismissAttention).toHaveBeenCalledWith('t', 'attention:3'))
    expect(onOpenTarget).not.toHaveBeenCalled()
  })

  it('offers the Inbox as the place nothing is lost', async () => {
    const user = userEvent.setup()
    const onOpenInbox = vi.fn()
    render(<AttentionInbox token="t" onOpenTarget={vi.fn()} onOpenInbox={onOpenInbox} />)
    await user.click(await screen.findByRole('button', { name: /1 unread notification/ }))

    await user.click(screen.getByRole('button', { name: 'Open Inbox' }))

    expect(onOpenInbox).toHaveBeenCalled()
  })

  it('shows the detail line so an error is diagnosable from the header', async () => {
    const user = userEvent.setup()
    render(<AttentionInbox token="t" onOpenTarget={vi.fn()} onOpenInbox={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /1 unread notification/ }))

    expect(screen.getByText(/turn budget was reached/)).toBeInTheDocument()
  })
})
