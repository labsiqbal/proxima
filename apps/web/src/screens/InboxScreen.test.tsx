import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InboxScreen } from './InboxScreen'
import { getInbox, readAllInbox, setInboxRead } from '../api/inbox'
import { actAttention } from '../api/master'

vi.mock('../api/inbox', () => ({
  getInbox: vi.fn(),
  readAllInbox: vi.fn(),
  setInboxRead: vi.fn(),
  dismissAttention: vi.fn(),
}))
vi.mock('../api/master', () => ({
  actAttention: vi.fn(),
  getMasterDecision: vi.fn(),
  deferMasterDecision: vi.fn(),
  resolveMasterDecision: vi.fn(),
}))

const failure = {
  id: 'task:9:failed', seq: 12, kind: 'task_outcome', title: 'Nightly build failed',
  target: { view: 'task', job_id: 9 }, inline_ok: false, actions: [], status: 'resolved',
  severity: 'error' as const, body: 'step 1 exited with code 2\n\nOpen the Task to read the full run output.',
  detail: {}, requires_action: false, read: false, created_at: '2026-01-02 10:00:00',
}
const review = {
  id: 'job:4', seq: 11, kind: 'job_review', title: 'Release needs review',
  target: { view: 'task', job_id: 4 }, inline_ok: true, actions: ['approve', 'reject'],
  status: 'open', severity: 'action' as const, body: '', detail: {},
  requires_action: true, read: true, created_at: '2026-01-01 10:00:00',
}
const page = (over: Record<string, unknown> = {}) => ({
  items: [failure, review], unread: 1, next_before: null, ...over,
})

describe('Inbox destination', () => {
  beforeEach(() => {
    vi.mocked(getInbox).mockReset().mockResolvedValue(page() as never)
    vi.mocked(setInboxRead).mockReset().mockResolvedValue({ ok: true, id: '', read: true })
    vi.mocked(readAllInbox).mockReset().mockResolvedValue({ ok: true, read: 1 })
    vi.mocked(actAttention).mockReset().mockResolvedValue({ ok: true, id: '', action: '' })
  })

  it('keeps every notification with its detail and its read state', async () => {
    render(<InboxScreen token="t" onOpenTarget={vi.fn()} />)

    expect(await screen.findByText('Nightly build failed')).toBeInTheDocument()
    expect(screen.getByText(/step 1 exited with code 2/)).toBeInTheDocument()
    expect(screen.getByText('Release needs review')).toBeInTheDocument()
    const rows = document.querySelectorAll('.inbox-item')
    expect(rows[0].className).toContain('is-unread')
    expect(rows[1].className).toContain('is-read')
  })

  it('filters to unread and clears the badge without discarding anything', async () => {
    const user = userEvent.setup()
    render(<InboxScreen token="t" onOpenTarget={vi.fn()} />)
    await screen.findByText('Nightly build failed')

    await user.click(screen.getByRole('button', { name: /^Unread/ }))
    await waitFor(() =>
      expect(vi.mocked(getInbox).mock.calls.at(-1)?.[1]).toMatchObject({ unread: true }),
    )

    await user.click(screen.getByRole('button', { name: 'Mark all read' }))
    expect(readAllInbox).toHaveBeenCalledWith('t')
  })

  it('opens a notification, marking it read on the way', async () => {
    const user = userEvent.setup()
    const onOpenTarget = vi.fn()
    render(<InboxScreen token="t" onOpenTarget={onOpenTarget} />)
    await screen.findByText('Nightly build failed')

    await user.click(screen.getByRole('button', { name: /Nightly build failed/ }))

    expect(setInboxRead).toHaveBeenCalledWith('t', 'task:9:failed', true)
    expect(onOpenTarget).toHaveBeenCalledWith(failure.target)
  })

  it('keeps an actionable item resolvable inside the Inbox', async () => {
    const user = userEvent.setup()
    render(<InboxScreen token="t" onOpenTarget={vi.fn()} />)
    await screen.findByText('Release needs review')

    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(actAttention).toHaveBeenCalledWith('t', 'job:4', 'approve')
  })

  it('lets the owner mark a notification unread again', async () => {
    const user = userEvent.setup()
    render(<InboxScreen token="t" onOpenTarget={vi.fn()} />)
    await screen.findByText('Release needs review')

    await user.click(screen.getAllByRole('button', { name: 'Mark unread' })[0])

    expect(setInboxRead).toHaveBeenCalledWith('t', 'job:4', false)
  })

  it('says so when there is nothing to read', async () => {
    vi.mocked(getInbox).mockResolvedValue({ items: [], unread: 0, next_before: null })
    render(<InboxScreen token="t" onOpenTarget={vi.fn()} />)

    expect(await screen.findByText(/Nothing here yet/i)).toBeInTheDocument()
  })

  // #158: "Attention items that genuinely need a decision keep their actionable
  // affordances inside the Inbox entry" - so the one kind with a real decision
  // form must not degrade to an Open button here.
  it('asks a Master decision in full rather than linking away', async () => {
    vi.mocked(getInbox).mockResolvedValue({
      items: [{
        ...review, id: 'attention:8', kind: 'master_decision', inline_ok: false, actions: [],
        title: 'Choose rollout window', read: false,
        decision: {
          id: 8, attention_item_id: 8, master_session_id: 3, origin_message_id: 21,
          requesting_job_id: 4, title: 'Choose rollout window',
          prompt: 'Which rollout window should the release use?',
          context: 'Both choices include two hours of planned downtime.',
          response_shape: { type: 'choice', choices: [{ id: 'sunday', label: 'Sunday 02:00 UTC' }] },
          state: 'pending', response: null, version: 1,
          created_at: '2026-01-01 00:00:00', updated_at: '2026-01-01 00:00:00',
          legacy_without_task: false, task: null,
        },
      }],
      unread: 1, next_before: null,
    } as never)
    render(<InboxScreen token="t" onOpenTarget={vi.fn()} />)

    expect(await screen.findByText('Which rollout window should the release use?')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Sunday 02:00 UTC' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open' })).not.toBeInTheDocument()
  })

  it('walks older pages with the server cursor', async () => {
    const user = userEvent.setup()
    vi.mocked(getInbox)
      .mockResolvedValueOnce(page({ next_before: 11 }) as never)
      .mockResolvedValueOnce({ items: [], unread: 1, next_before: null })
    render(<InboxScreen token="t" onOpenTarget={vi.fn()} />)
    await screen.findByText('Nightly build failed')

    await user.click(screen.getByRole('button', { name: 'Load older' }))

    expect(vi.mocked(getInbox).mock.calls.at(-1)?.[1]).toMatchObject({ before: 11 })
  })
})
