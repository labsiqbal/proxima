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
  const [error, setError] = React.useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (isSetup) {
      if (pw.length < 8) { setError('Password must be at least 8 characters.'); return }
      if (pw !== confirm) { setError('Passwords don’t match.'); return }
    } else if (!pw) { setError('Enter your password.'); return }
    setBusy(true)
    try {
      onAuthed(isSetup ? await apiSetPassword(pw) : await apiLogin(pw))
    } catch {
      setError(isSetup ? 'Could not set the password. Try a longer one.' : 'Incorrect password.')
      setBusy(false)
    }
  }

  return (
    <div className="center-screen">
      <form className="auth-card" onSubmit={submit}>
        <ProximaMark className="proxima-mark-boot" label="Proxima" />
        <h1 className="auth-title">{isSetup ? 'Set a password' : 'Welcome back'}</h1>
        <p className="auth-sub">{isSetup ? 'Protect your cockpit — you’ll enter this to sign in.' : 'Enter your password to unlock the cockpit.'}</p>
        <input className="auth-input" type="password" autoFocus placeholder="Password" value={pw}
          onChange={e => setPw(e.target.value)} autoComplete={isSetup ? 'new-password' : 'current-password'} />
        {isSetup && <input className="auth-input" type="password" placeholder="Confirm password" value={confirm}
          onChange={e => setConfirm(e.target.value)} autoComplete="new-password" />}
        {error && <p className="auth-error">{error}</p>}
        <button className="primary-button auth-submit" type="submit" disabled={busy}>
          {busy ? 'Please wait…' : isSetup ? 'Set password & enter' : 'Log in'}
        </button>
      </form>
    </div>
  )
}
