import React from 'react'

type RunnerReady = { id: string; displayName: string; installed: boolean; ready: boolean; authHint: string }

// Warns only when the runner the ACTIVE profile actually uses isn't ready.
// Proxima is runner-agnostic: if you're on Claude Code and it's ready, no nag.
export function HermesBanner({ token, runnerId }: { token: string; runnerId?: string | null }) {
  const [readiness, setReadiness] = React.useState<Record<string, RunnerReady> | null>(null)
  const readinessSeq = React.useRef(0)
  React.useEffect(() => {
    if (!token) {
      readinessSeq.current += 1
      setReadiness(null)
      return
    }
    const seq = ++readinessSeq.current
    fetch('/api/runners/detect', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(b => {
        if (seq === readinessSeq.current) setReadiness(b.runnerReadiness ?? null)
      })
      .catch(() => {
        if (seq === readinessSeq.current) setReadiness(null)
      })
    return () => { if (seq === readinessSeq.current) readinessSeq.current += 1 }
  }, [token])
  if (!readiness || !runnerId) return null
  const active = readiness[runnerId]
  if (!active || active.ready) return null
  return <div className="hermes-banner" role="status">⚠ {active.displayName} runner not ready{active.authHint ? ` — ${active.authHint}` : ''}</div>
}
