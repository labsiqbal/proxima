import React from 'react'
import { ProximaMark } from '../components/brand/ProximaMark'
import { setPassword as apiSetPassword, login as apiLogin } from '../api/auth'
import type { User } from '../types'

// First-run "set a password" and the returning "log in" gate. Same card, two modes.
export function AuthGate({ mode, onAuthed }: { mode: 'setup' | 'login'; onAuthed: (s: { token: string; user: User }) => void }) {
  const isSetup = mode === 'setup'
  const [pw, setPw] = React.useState('')
  const [confirm, setConfirm] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<{ message: string; field: 'password' | 'confirmation' } | null>(null)
  const passwordRef = React.useRef<HTMLInputElement>(null)
  const confirmationRef = React.useRef<HTMLInputElement>(null)

  const reportError = (message: string, field: 'password' | 'confirmation') => {
    setError({ message, field })
    const target = field === 'confirmation' ? confirmationRef : passwordRef
    target.current?.focus()
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (isSetup) {
      if (pw.length < 8) { reportError('Password must be at least 8 characters.', 'password'); return }
      if (pw !== confirm) { reportError('Passwords don’t match.', 'confirmation'); return }
    } else if (!pw) { reportError('Enter your password.', 'password'); return }
    setBusy(true)
    try {
      onAuthed(isSetup ? await apiSetPassword(pw) : await apiLogin(pw))
    } catch {
      reportError(isSetup ? 'Could not set the password. Try a longer one.' : 'Incorrect password.', 'password')
      setBusy(false)
    }
  }

  return (
    <main className="center-screen">
      <form className="auth-card" onSubmit={submit}>
        <ProximaMark className="proxima-mark-boot" label="Proxima" />
        <h1 className="auth-title">{isSetup ? 'Set a password' : 'Welcome back'}</h1>
        <p className="auth-sub">{isSetup ? 'Protect your cockpit — you’ll enter this to sign in.' : 'Enter your password to unlock the cockpit.'}</p>
        <input type="text" name="username" autoComplete="username" value="owner" readOnly tabIndex={-1} aria-hidden="true" className="sr-only" />
        <input ref={passwordRef} className="auth-input" type="password" name="password" autoFocus placeholder="Password" value={pw}
          onChange={e => { setPw(e.target.value); if (error?.field === 'password') setError(null) }}
          autoComplete={isSetup ? 'new-password' : 'current-password'}
          aria-invalid={error?.field === 'password' || undefined}
          aria-describedby={error?.field === 'password' ? 'auth-error' : undefined} />
        {isSetup && <input ref={confirmationRef} className="auth-input" type="password" name="password-confirmation" placeholder="Confirm password" value={confirm}
          onChange={e => { setConfirm(e.target.value); if (error?.field === 'confirmation') setError(null) }}
          autoComplete="new-password"
          aria-invalid={error?.field === 'confirmation' || undefined}
          aria-describedby={error?.field === 'confirmation' ? 'auth-error' : undefined} />}
        {error && <p id="auth-error" className="auth-error" role="alert">{error.message}</p>}
        <button className="primary-button auth-submit" type="submit" disabled={busy}>
          {busy ? 'Please wait…' : isSetup ? 'Set password & enter' : 'Log in'}
        </button>
      </form>
    </main>
  )
}
