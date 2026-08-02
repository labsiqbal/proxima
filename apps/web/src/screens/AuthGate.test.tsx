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
    vi.mocked(setPassword).mockResolvedValue({ token: 't', user: { id: 1, username: 'owner', os_user: 'owner' } })
    render(<AuthGate mode="setup" onAuthed={onAuthed} />)
    const user = userEvent.setup()
    expect(screen.getByPlaceholderText('Password')).toHaveAttribute('name', 'password')
    expect(screen.getByPlaceholderText('Confirm password'))
      .toHaveAttribute('name', 'password-confirmation')

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
    const password = screen.getByPlaceholderText('Password')
    const confirmation = screen.getByPlaceholderText('Confirm password')
    await user.type(password, 'longenough1')
    await user.type(confirmation, 'different99')
    await user.click(screen.getByRole('button', { name: /set password/i }))
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/don.t match/i)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(confirmation).toHaveFocus()
    expect(confirmation).toHaveAttribute('aria-invalid', 'true')
    expect(confirmation).not.toHaveAttribute('aria-describedby')
    expect(setPassword).not.toHaveBeenCalled()

    await user.keyboard('{Enter}')
    const repeatedAlert = screen.getByRole('alert')
    expect(repeatedAlert).not.toBe(alert)
    expect(repeatedAlert).toHaveTextContent(/don.t match/i)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(confirmation).toHaveFocus()
    expect(setPassword).not.toHaveBeenCalled()
  })

  it('login: surfaces an error on the wrong password', async () => {
    const onAuthed = vi.fn()
    vi.mocked(login).mockRejectedValue(new Error('nope'))
    render(<AuthGate mode="login" onAuthed={onAuthed} />)
    const user = userEvent.setup()
    const password = screen.getByPlaceholderText('Password')
    await user.type(password, 'whatever1')
    await user.click(screen.getByRole('button', { name: /log in/i }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/incorrect password/i)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(password).toHaveFocus()
    expect(password).toHaveAttribute('aria-invalid', 'true')
    expect(password).not.toHaveAttribute('aria-describedby')
    expect(onAuthed).not.toHaveBeenCalled()
  })

  it('exposes one main landmark and hidden single-owner identity metadata', () => {
    const { container } = render(<AuthGate mode="login" onAuthed={vi.fn()} />)

    expect(screen.getAllByRole('main')).toHaveLength(1)
    const ownerIdentity = container.querySelector('input[name="username"]')
    expect(ownerIdentity).toHaveAttribute('autocomplete', 'username')
    expect(ownerIdentity).toHaveAttribute('value', 'owner')
    expect(ownerIdentity).toHaveAttribute('tabindex', '-1')
  })
})
