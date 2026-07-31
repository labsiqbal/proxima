import '@testing-library/jest-dom/vitest'
import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AttentionInbox } from './AttentionInbox'
import {
  actAttention,
  deferMasterDecision,
  getAttention,
  getMasterDecision,
  resolveMasterDecision,
} from '../../api/master'
import { FRESH_FAILED_REVIEW_ATTENTION } from '../../testFixtures/failedReviewRun'

vi.mock('../../api/master', () => ({
  getAttention: vi.fn(),
  actAttention: vi.fn(),
  getMasterDecision: vi.fn(),
  deferMasterDecision: vi.fn(),
  resolveMasterDecision: vi.fn(),
}))

const item = {
  id: 'job:4', kind: 'job_review', title: 'Release needs review',
  target: { view: 'task', job_id: 4 }, inline_ok: true,
  actions: ['approve', 'reject'], status: 'open', created_at: '2026-01-01',
}
const decision = {
  id: 8,
  attention_item_id: 11,
  master_session_id: 3,
  origin_message_id: 21,
  requesting_job_id: 4,
  title: 'Choose rollout window',
  prompt: 'Which rollout window should the release use?',
  context: 'Both choices include two hours of planned downtime.',
  response_shape: {
    type: 'choice' as const,
    choices: [
      { id: 'saturday', label: 'Saturday 02:00 UTC' },
      { id: 'sunday', label: 'Sunday 02:00 UTC' },
    ],
  },
  state: 'pending' as const,
  response: null,
  version: 1,
  created_at: '2026-01-01 00:00:00',
  updated_at: '2026-01-01 00:00:00',
  legacy_without_task: false,
  task: {
    id: 4,
    title: 'Prepare rollout',
    status: 'review',
    engine: 'legacy',
  },
}
const decisionItem = {
  id: 'attention:11',
  kind: 'master_decision',
  title: decision.title,
  target: { view: 'master', decision_id: 8 },
  inline_ok: false,
  actions: [],
  status: 'open',
  created_at: decision.created_at,
  decision,
}

function InboxHarness({
  onOpenChange = vi.fn(),
  onOpenTarget = vi.fn(),
}: {
  onOpenChange?: (open: boolean) => void
  onOpenTarget?: React.ComponentProps<typeof AttentionInbox>['onOpenTarget']
}) {
  const [open, setOpen] = React.useState(false)
  return (
    <AttentionInbox
      token="token"
      open={open}
      onOpenTarget={onOpenTarget}
      onOpenChange={next => {
        setOpen(next)
        onOpenChange(next)
      }}
    />
  )
}

describe('AttentionInbox', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-31T05:00:30Z'))
    vi.mocked(getAttention).mockResolvedValue({ items: [item], count: 1 })
    vi.mocked(actAttention).mockResolvedValue({ ok: true, id: 'job:4', action: 'approve' })
    vi.mocked(getMasterDecision).mockResolvedValue(decision)
    vi.mocked(deferMasterDecision).mockResolvedValue({
      ...decision,
      state: 'deferred',
      version: 2,
    })
    vi.mocked(resolveMasterDecision).mockResolvedValue({
      ...decision,
      state: 'resolved',
      version: 2,
      response: { value: 'sunday', label: 'Sunday 02:00 UTC' },
      resolved_at: '2026-01-01 00:01:00',
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('hides the trigger when the inbox is empty so it does not look like an active alarm', async () => {
    vi.mocked(getAttention).mockResolvedValue({ items: [], count: 0 })
    const { container } = render(<InboxHarness />)
    await waitFor(() => expect(getAttention).toHaveBeenCalled())
    expect(container.querySelector('.attention-inbox')).toBeNull()
    expect(screen.queryByRole('button', { name: /attention item/ })).not.toBeInTheDocument()
    expect(screen.queryByText('!')).not.toBeInTheDocument()
  })

  it('shows a needs-you trigger with count when there are open items', async () => {
    render(<InboxHarness />)
    const trigger = await screen.findByRole('button', { name: '1 attention item' })
    expect(trigger).toHaveClass('has-attention')
    expect(trigger).toHaveTextContent('!')
    expect(trigger.querySelector('b')).toHaveTextContent('1')
  })

  it('deep-links every item and restricts inline controls to supplied actions', async () => {
    const user = userEvent.setup()
    const openTarget = vi.fn()
    function TargetHarness() {
      const [open, setOpen] = React.useState(false)
      return <AttentionInbox token="token" open={open} onOpenTarget={openTarget} onOpenChange={setOpen} />
    }
    render(<TargetHarness />)
    await waitFor(() => expect(screen.getByRole('button', { name: '1 attention item' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '1 attention item' }))

    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Release needs review/ }))
    expect(openTarget).toHaveBeenCalledWith({ view: 'task', job_id: 4 })
  })

  it('runs a safe inline action once and refreshes the inbox', async () => {
    const user = userEvent.setup()
    render(<InboxHarness />)
    await user.click(await screen.findByRole('button', { name: '1 attention item' }))
    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(actAttention).toHaveBeenCalledWith('token', 'job:4', 'approve')
    expect(getAttention).toHaveBeenCalledTimes(2)
  })

  it('uses the same failed status and fresh run age as Workflows and Tasks', async () => {
    vi.mocked(getAttention).mockResolvedValue({
      items: [FRESH_FAILED_REVIEW_ATTENTION],
      count: 1,
    })
    const user = userEvent.setup()
    render(<InboxHarness />)

    await user.click(await screen.findByRole('button', { name: '1 attention item' }))

    expect(screen.getByText(/Failed · Just now/)).toBeInTheDocument()
    expect(screen.queryByText(/Review ·/)).not.toBeInTheDocument()
  })

  it('resolves a bounded Master decision without losing its question or links', async () => {
    vi.mocked(getAttention).mockResolvedValue({
      items: [decisionItem],
      count: 1,
    })
    const user = userEvent.setup()
    const openTarget = vi.fn()
    render(<InboxHarness onOpenTarget={openTarget} />)
    await user.click(
      await screen.findByRole('button', { name: '1 attention item' }),
    )

    expect(screen.getByText(decision.prompt)).toBeInTheDocument()
    expect(screen.getByText(decision.context)).toBeInTheDocument()
    await user.click(
      screen.getByRole('radio', { name: 'Sunday 02:00 UTC' }),
    )
    await user.click(screen.getByRole('button', { name: 'Send decision' }))
    expect(resolveMasterDecision).toHaveBeenCalledWith(
      'token',
      8,
      1,
      'sunday',
    )

    await user.click(screen.getByRole('button', { name: 'Open Task #4' }))
    expect(openTarget).toHaveBeenCalledWith({
      view: 'task',
      job_id: 4,
      engine: 'legacy',
    })
  })

  it('opens the Master conversation at the durable origin message', async () => {
    vi.mocked(getAttention).mockResolvedValue({
      items: [decisionItem],
      count: 1,
    })
    const user = userEvent.setup()
    const openTarget = vi.fn()
    render(<InboxHarness onOpenTarget={openTarget} />)
    await user.click(
      await screen.findByRole('button', { name: '1 attention item' }),
    )
    await user.click(
      screen.getByRole('button', { name: 'Open Master conversation' }),
    )
    expect(openTarget).toHaveBeenCalledWith({
      view: 'master',
      origin_message_id: 21,
    })
  })

  it('keeps bare supervisor start-failure rows on the generic attention path', async () => {
    const bareItem = {
      id: 'attention:99',
      kind: 'master_decision',
      title: 'Master could not start queued work',
      target: { view: 'master', job_id: 7 },
      inline_ok: false,
      actions: [],
      status: 'open',
      created_at: '2026-01-01 00:00:00',
    }
    vi.mocked(getAttention).mockResolvedValue({
      items: [bareItem],
      count: 1,
    })
    const user = userEvent.setup()
    const openTarget = vi.fn()
    render(<InboxHarness onOpenTarget={openTarget} />)
    await user.click(
      await screen.findByRole('button', { name: '1 attention item' }),
    )

    expect(
      screen.getByText('Master could not start queued work'),
    ).toBeInTheDocument()
    expect(screen.queryByText('Send decision')).not.toBeInTheDocument()
    await user.click(
      screen.getByRole('button', { name: /Master could not start queued work/ }),
    )
    expect(openTarget).toHaveBeenCalledWith({ view: 'master', job_id: 7 })
  })

  it('defers a Master decision through its specialized state path', async () => {
    vi.mocked(getAttention).mockResolvedValue({
      items: [decisionItem],
      count: 1,
    })
    const user = userEvent.setup()
    render(<InboxHarness />)
    await user.click(
      await screen.findByRole('button', { name: '1 attention item' }),
    )
    await user.click(screen.getByRole('button', { name: 'Decide later' }))

    expect(deferMasterDecision).toHaveBeenCalledWith('token', 8, 1)
    expect(getAttention).toHaveBeenCalledTimes(2)
  })

  it('reports keyboard disclosure state to the shell overlay owner', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<InboxHarness onOpenChange={onOpenChange} />)
    const trigger = await screen.findByRole('button', { name: '1 attention item' })
    trigger.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(onOpenChange).toHaveBeenLastCalledWith(true))
    await user.keyboard('{Escape}')
    await waitFor(() => expect(onOpenChange).toHaveBeenLastCalledWith(false))
  })
})
