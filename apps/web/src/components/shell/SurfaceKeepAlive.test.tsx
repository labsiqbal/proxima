import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { describe, expect, it } from 'vitest'

/**
 * Multitask foundation (M1): primary surfaces must not destroy local UI state on leave.
 * This proves the keep-alive pattern used for Chat (hidden pane stays mounted).
 */
function KeepAliveDemo() {
  const [view, setView] = React.useState<'chat' | 'tasks'>('chat')
  const [draft, setDraft] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  return (
    <div>
      <button type="button" onClick={() => setView('chat')}>Go Chat</button>
      <button type="button" onClick={() => setView('tasks')}>Go Tasks</button>
      <div className="surface-pane" hidden={view !== 'chat'} aria-hidden={view !== 'chat'}>
        <label>
          Draft
          <textarea
            aria-label="Chat draft"
            value={draft}
            onChange={e => setDraft(e.target.value)}
          />
        </label>
        <button type="button" onClick={() => setBusy(true)}>
          {busy ? 'Running…' : 'Start run'}
        </button>
        {busy && <span data-testid="run-status">in-flight</span>}
      </div>
      {view === 'tasks' && <div>Tasks index</div>}
    </div>
  )
}

describe('surface keep-alive (Chat leave/return)', () => {
  it('preserves draft text and in-flight run after switching views and back', async () => {
    const user = userEvent.setup()
    render(<KeepAliveDemo />)

    await user.type(screen.getByLabelText('Chat draft'), 'half-written prompt')
    await user.click(screen.getByRole('button', { name: 'Start run' }))
    expect(screen.getByTestId('run-status')).toHaveTextContent('in-flight')

    await user.click(screen.getByRole('button', { name: 'Go Tasks' }))
    expect(screen.getByText('Tasks index')).toBeInTheDocument()
    // Chat pane stays in the document (mounted) while hidden.
    expect(screen.getByLabelText('Chat draft')).toBeInTheDocument()
    expect(screen.getByLabelText('Chat draft')).not.toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Go Chat' }))
    expect(screen.getByLabelText('Chat draft')).toBeVisible()
    expect(screen.getByLabelText('Chat draft')).toHaveValue('half-written prompt')
    expect(screen.getByTestId('run-status')).toHaveTextContent('in-flight')
  })
})

describe('surface keep-alive (multi primary surfaces)', () => {
  it('preserves Alpha and Tasks panes after leave/return', async () => {
    const user = userEvent.setup()
    function MultiKeepAlive() {
      const [view, setView] = React.useState<'alpha' | 'activity' | 'chat'>('alpha')
      const [alphaDraft, setAlphaDraft] = React.useState('delegate me')
      const [taskFilter, setTaskFilter] = React.useState('all')
      return (
        <div>
          <button type="button" onClick={() => setView('alpha')}>Go Alpha</button>
          <button type="button" onClick={() => setView('activity')}>Go Tasks</button>
          <button type="button" onClick={() => setView('chat')}>Go Chat</button>
          <div className="surface-pane" hidden={view !== 'alpha'} aria-hidden={view !== 'alpha'}>
            <label>Alpha draft<textarea aria-label="Alpha draft" value={alphaDraft} onChange={e => setAlphaDraft(e.target.value)} /></label>
          </div>
          <div className="surface-pane" hidden={view !== 'activity'} aria-hidden={view !== 'activity'}>
            <label>Filter<input aria-label="Task filter" value={taskFilter} onChange={e => setTaskFilter(e.target.value)} /></label>
          </div>
          {view === 'chat' && <div>Chat surface</div>}
        </div>
      )
    }
    render(<MultiKeepAlive />)
    expect(screen.getByLabelText('Alpha draft')).toHaveValue('delegate me')
    await user.click(screen.getByRole('button', { name: 'Go Tasks' }))
    await user.clear(screen.getByLabelText('Task filter'))
    await user.type(screen.getByLabelText('Task filter'), 'review')
    await user.click(screen.getByRole('button', { name: 'Go Chat' }))
    expect(screen.getByText('Chat surface')).toBeInTheDocument()
    expect(screen.getByLabelText('Alpha draft')).toBeInTheDocument()
    expect(screen.getByLabelText('Task filter')).toHaveValue('review')
    await user.click(screen.getByRole('button', { name: 'Go Alpha' }))
    expect(screen.getByLabelText('Alpha draft')).toBeVisible()
    expect(screen.getByLabelText('Alpha draft')).toHaveValue('delegate me')
  })
})

