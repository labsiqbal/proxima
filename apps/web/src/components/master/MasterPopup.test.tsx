import '@testing-library/jest-dom/vitest'
import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMasterState } from '../../master/MasterStateProvider'
import { MasterPopup } from './MasterPopup'
import { MasterToastRegion } from './MasterToastRegion'

vi.mock('../../master/MasterStateProvider', () => ({ useMasterState: vi.fn() }))
vi.mock('./MasterConversation', () => ({
  MasterConversation: () => <button type="button">Thread action</button>,
}))
vi.mock('./MasterComposer', () => ({
  MasterComposer: () => <textarea aria-label="Message Master" />,
}))

function PopupHarness({ pending = false }: { pending?: boolean }) {
  const [popup, setPopup] = React.useState({
    open: false,
    preferredCorner: 'right' as const,
  })
  const actions = React.useMemo(() => ({
    openPopup: () => setPopup(current => ({ ...current, open: true })),
    closePopup: () => setPopup(current => ({ ...current, open: false })),
    togglePopup: () => setPopup(current => ({ ...current, open: !current.open })),
    setPopupCorner: (preferredCorner: 'left' | 'right') => {
      setPopup(current => ({ ...current, preferredCorner }))
    },
  }), [])
  vi.mocked(useMasterState).mockReturnValue({
    enabled: true,
    desk: {
      session: { id: 9 },
      focus: {
        pending,
        pending_container_id: pending ? 21 : null,
      },
    },
    connection: { state: 'connected' },
    unread: { count: 2 },
    popup,
    focus: { mode: 'fleet', containerId: null },
    fleet: {
      containers: pending
        ? [{ id: 21, name: 'Acme', identity_label: 'Acme' }]
        : [],
    },
    actions,
  } as never)
  return (
    <MasterPopup
      token="token"
      available
      onOpenHome={vi.fn()}
      onOpenJob={vi.fn()}
    />
  )
}

describe('MasterPopup', () => {
  beforeEach(() => vi.clearAllMocks())

  it('opens by shortcut, traps focus, closes on Escape, and restores the trigger', async () => {
    const user = userEvent.setup()
    render(<PopupHarness />)
    const trigger = screen.getByRole('button', { name: 'Open Master popup' })
    trigger.focus()

    await user.keyboard('{Control>}{Shift>}m{/Shift}{/Control}')
    const dialog = await screen.findByRole('dialog', { name: 'Master' })
    expect(dialog).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Message Master' })).toHaveFocus()
    })

    await user.tab()
    expect(screen.getByRole('button', { name: 'Move popup to bottom left' }))
      .toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Open Master popup' })).toHaveFocus()
    })
  })

  it('switches the persisted corner control without remounting the dialog', async () => {
    const user = userEvent.setup()
    render(<PopupHarness />)
    await user.click(screen.getByRole('button', { name: 'Open Master popup' }))
    const dialog = screen.getByRole('dialog', { name: 'Master' })
    await user.click(screen.getByRole('button', { name: 'Move popup to bottom left' }))
    expect(screen.getByRole('button', { name: 'Move popup to bottom right' }))
      .toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Master' })).toBe(dialog)
  })

  it('shows a pending Focus inside the shared popup', async () => {
    const user = userEvent.setup()
    render(<PopupHarness pending />)

    await user.click(screen.getByRole('button', { name: 'Open Master popup' }))

    expect(screen.getByText('Pending Focus: Acme. Applies after this turn.'))
      .toBeInTheDocument()
  })
})

describe('MasterToastRegion', () => {
  it('uses live priority, dismisses by keyboard, and never steals focus', async () => {
    const dismissToast = vi.fn()
    vi.mocked(useMasterState).mockReturnValue({
      toasts: [
        {
          id: 1,
          sourceKey: 'one',
          title: 'Task complete',
          body: 'Durable result ready.',
          tone: 'success',
          priority: 'polite',
        },
        {
          id: 2,
          sourceKey: 'two',
          title: 'Satpam escalation',
          body: 'Owner decision needed.',
          tone: 'danger',
          priority: 'assertive',
        },
      ],
      popup: { open: false, preferredCorner: 'right' },
      actions: { dismissToast },
    } as never)
    const user = userEvent.setup()
    render(
      <>
        <button type="button">Stable focus</button>
        <MasterToastRegion />
      </>,
    )
    const stable = screen.getByRole('button', { name: 'Stable focus' })
    stable.focus()
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByRole('alert')).toHaveAttribute('aria-live', 'assertive')
    expect(stable).toHaveFocus()

    const dismiss = screen.getByRole('button', { name: 'Dismiss Task complete' })
    dismiss.focus()
    await user.keyboard('{Enter}')
    expect(dismissToast).toHaveBeenCalledWith(1)
  })

  it('does not overlap the popup or a safety-critical shell overlay', () => {
    vi.mocked(useMasterState).mockReturnValue({
      toasts: [{
        id: 1,
        sourceKey: 'one',
        title: 'Task complete',
        body: 'Durable result ready.',
        tone: 'success',
        priority: 'polite',
      }],
      popup: { open: true, preferredCorner: 'right' },
      actions: { dismissToast: vi.fn() },
    } as never)
    const view = render(<MasterToastRegion />)
    expect(screen.queryByLabelText('Master notifications')).not.toBeInTheDocument()

    vi.mocked(useMasterState).mockReturnValue({
      ...vi.mocked(useMasterState)(),
      popup: { open: false, preferredCorner: 'right' },
    } as never)
    view.rerender(<MasterToastRegion available={false} />)
    expect(screen.queryByLabelText('Master notifications')).not.toBeInTheDocument()
  })
})
