import React from 'react'
import { applyUpdate, checkForUpdate, getUpdateStatus, type UpdateStatus } from '../api/updates'

/** Applying-phase state: set → overlay shown; failedLog/timedOut flip it to error. */
export type ApplyState = { target: string; failedLog?: string | null; timedOut?: boolean }

const POLL_MS = 2000
const APPLY_TIMEOUT_MS = 5 * 60_000

// One place owns update state (App) — sidebar pill, modal, overlay and the
// Settings panel all read the same object, so a "Check now" in Settings also
// lights the sidebar pill.
export function useUpdateStatus(token: string) {
  const [status, setStatus] = React.useState<UpdateStatus | null>(null)
  const [checking, setChecking] = React.useState(false)
  const [modalOpen, setModalOpen] = React.useState(false)
  const [applying, setApplying] = React.useState<ApplyState | null>(null)
  const mountedRef = React.useRef(true)
  React.useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])

  const refresh = React.useCallback(async () => {
    if (!token) return
    try { const s = await getUpdateStatus(token); if (mountedRef.current) setStatus(s) } catch { /* older server / restarting — stay silent */ }
  }, [token])
  React.useEffect(() => { void refresh() }, [refresh])

  const check = React.useCallback(async () => {
    if (!token) return
    setChecking(true)
    try { const s = await checkForUpdate(token); if (mountedRef.current) setStatus(s) } catch { /* silent */ } finally { if (mountedRef.current) setChecking(false) }
  }, [token])

  const apply = React.useCallback(async () => {
    if (!token) return
    const r = await applyUpdate(token) // ApiError propagates — the modal shows it
    if (!mountedRef.current) return
    setModalOpen(false)
    setApplying({ target: r.target })
    const startedAt = Date.now()
    const poll = async () => {
      if (!mountedRef.current) return
      if (Date.now() - startedAt > APPLY_TIMEOUT_MS) { setApplying(a => (a ? { ...a, timedOut: true } : a)); return }
      try {
        const res = await fetch('/api/health', { cache: 'no-store' })
        if (!mountedRef.current) return
        if (res.ok) {
          const j = (await res.json()) as { version?: string }
          if (j.version === r.target) { window.location.reload(); return }
          // Server is up but still the old version: either mid-build (fine) or failed.
          const st = await getUpdateStatus(token).catch(() => null)
          if (!mountedRef.current) return
          if (st && st.state === 'failed') { setApplying({ target: r.target, failedLog: st.log_tail }); return }
        }
      } catch { /* server restarting — keep polling */ }
      window.setTimeout(() => { void poll() }, POLL_MS)
    }
    window.setTimeout(() => { void poll() }, POLL_MS)
  }, [token])

  return {
    status,
    refresh,
    check,
    checking,
    modalOpen,
    openModal: React.useCallback(() => setModalOpen(true), []),
    closeModal: React.useCallback(() => setModalOpen(false), []),
    apply,
    applying,
    dismissApplying: React.useCallback(() => setApplying(null), []),
  }
}
