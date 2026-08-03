import React from 'react'
import { appStatus, type AppStatus } from '../../api/files'
import { usePolling } from '../../hooks/usePolling'

// One poll of the managed app's status, shared by both halves of Run & Preview
// (#147): the dock's controls (`AppRunner`) and the main-window viewport
// (`AppViewport`). They are not parent and child - either can be mounted
// without the other - so each holds its own subscription, but the guard logic
// is written once here: a stale in-flight response never overwrites a newer
// one, an unmounted consumer never sets state, and switching projects resets to
// `stopped` rather than showing the previous project's app.
//
// `refresh` is the manual poke an action needs (Run and Stop want the new state
// now, not on the next tick). `setStatus` is for the one case where the client
// knows better than the last poll: "Change port" stops the app and puts the
// panel back in its stopped state without waiting to be told. `onStatus` fires
// in the same batch as the state change rather than in a later effect, because
// a consumer that derives editable form state from a poll (the port box on a
// conflict) must have it before the owner's next click reads it.
export function useAppStatus(token: string, slug: string, onStatus?: (status: AppStatus) => void): {
  status: AppStatus
  setStatus: React.Dispatch<React.SetStateAction<AppStatus>>
  refresh: () => Promise<void>
} {
  const [status, setStatus] = React.useState<AppStatus>({ state: 'stopped', running: false, ready: false })
  const mountedRef = React.useRef(true)
  const seqRef = React.useRef(0)
  const onStatusRef = React.useRef(onStatus)
  onStatusRef.current = onStatus

  React.useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; seqRef.current += 1 }
  }, [])

  React.useEffect(() => {
    seqRef.current += 1
    setStatus({ state: 'stopped', running: false, ready: false })
  }, [slug])

  const refresh = React.useCallback(async () => {
    const seq = ++seqRef.current
    try {
      const next = await appStatus(token, slug)
      if (mountedRef.current && seq === seqRef.current) {
        setStatus(next)
        onStatusRef.current?.(next)
      }
    } catch { /* a stopped or booting app is represented by the last known status */ }
  }, [token, slug])

  usePolling(refresh, 2000, { restartKey: `${token}:${slug}` })
  return { status, setStatus, refresh }
}
