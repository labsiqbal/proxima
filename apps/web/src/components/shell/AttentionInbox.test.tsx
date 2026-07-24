import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AttentionInbox } from './AttentionInbox'
import { actAttention, getAttention } from '../../api/alpha'

vi.mock('../../api/alpha', () => ({ getAttention: vi.fn(), actAttention: vi.fn() }))

const item = {
  id: 'job:4', kind: 'job_review', title: 'Release needs review',
  target: { view: 'task', job_id: 4 }, inline_ok: true,
  actions: ['approve', 'reject'], status: 'open', created_at: '2026-01-01',
}

describe('AttentionInbox', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getAttention).mockResolvedValue({ items: [item], count: 1 })
    vi.mocked(actAttention).mockResolvedValue({ ok: true, id: 'job:4', action: 'approve' })
  })

  it('deep-links every item and restricts inline controls to supplied actions', async () => {
    const user = userEvent.setup()
    const openTarget = vi.fn()
    render(<AttentionInbox token="token" onOpenTarget={openTarget} />)
    await waitFor(() => expect(screen.getByRole('button', { name: '1 attention item' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '1 attention item' }))

    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Release needs review/ }))
    expect(openTarget).toHaveBeenCalledWith({ view: 'task', job_id: 4 })
  })

  it('runs a safe inline action once and refreshes the inbox', async () => {
    const user = userEvent.setup()
    render(<AttentionInbox token="token" onOpenTarget={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: '1 attention item' }))
    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(actAttention).toHaveBeenCalledWith('token', 'job:4', 'approve')
    expect(getAttention).toHaveBeenCalledTimes(2)
  })
})
