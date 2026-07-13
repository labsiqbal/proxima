import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthGate } from './AuthGate'

vi.mock('../api/auth', () => ({ setPassword: vi.fn(), login: vi.fn() }))
import { setPassword, login } from '../api/auth'

describe('AuthGate', () => {
  beforeEach(() => vi.clearAllMocks())

  it('setup: rejects a short password (no API call), then submits a valid one', async () => {
    const onAuthed = vi.fn()
    vi.mocked(setPassword).mockResolvedValue({ token: 't', user: { id: 1, username: 'owner', role: 'environment_admin', os_user: 'owner' } })
    render(<AuthGate mode="setup" onAuthed={onAuthed} />)
    const user = userEvent.setup()

    await user.type(screen.getByPlaceholderText('Password'), 'short')
    await user.type(screen.getByPlaceholderText('Confirm password'), 'short')
    await user.click(screen.getByRole('button', { name: /set password/i }))
    expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument()
    expect(setPassword).not.toHaveBeenCalled()

    await user.clear(screen.getByPlaceholderText('Password'))
    await user.clear(screen.getByPlaceholderText('Confirm password'))
    await user.type(screen.getByPlaceholderText('Password'), 'longenough1')
    await user.type(screen.getByPlaceholderText('Confirm password'), 'longenough1')
    await user.click(screen.getByRole('button', { name: /set password/i }))
    await waitFor(() => expect(onAuthed).toHaveBeenCalledWith(expect.objectContaining({ token: 't' })))
  })

  it('setup: rejects mismatched confirmation', async () => {
    render(<AuthGate mode="setup" onAuthed={vi.fn()} />)
    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Password'), 'longenough1')
    await user.type(screen.getByPlaceholderText('Confirm password'), 'different99')
    await user.click(screen.getByRole('button', { name: /set password/i }))
    expect(screen.getByText(/don.t match/i)).toBeInTheDocument()
    expect(setPassword).not.toHaveBeenCalled()
  })

  it('login: surfaces an error on the wrong password', async () => {
    const onAuthed = vi.fn()
    vi.mocked(login).mockRejectedValue(new Error('nope'))
    render(<AuthGate mode="login" onAuthed={onAuthed} />)
    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Password'), 'whatever1')
    await user.click(screen.getByRole('button', { name: /log in/i }))
    await waitFor(() => expect(screen.getByText(/incorrect password/i)).toBeInTheDocument())
    expect(onAuthed).not.toHaveBeenCalled()
  })
})
