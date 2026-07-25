import React from 'react'
import { getAlphaSettings, saveAlphaSettings } from '../../api/alpha'

const STEPS = [
  { eyebrow: 'Primary loop', title: 'Welcome to Proxima', body: 'The taught path is Chat → Tasks → Workflows → Archive. Use Chat for hands-on work, watch durable runs in Tasks, keep patterns in Workflows, and find deliverables in Archive.' },
  { eyebrow: 'Hands-on', title: 'Chat keeps you close', body: 'Talk through the work, watch tools run, open outputs in the ArtifactViewer, and restore file-changing turns when you need to roll them back.' },
  { eyebrow: 'Delegate', title: 'Alpha is the side path', body: 'Alpha creates durable Autonomous jobs, dispatches up to three workers, and queues the rest. Prefer Chat when you want to stay in the thread; use Alpha when you want outcomes delegated.' },
  { eyebrow: 'Watch & keep', title: 'Tasks and Workflows', body: 'Tasks holds execution and diff review. When a pattern is worth repeating, save it as a Workflow (manual or scheduled) and re-run without re-explaining.' },
  { eyebrow: 'Deliverables', title: 'Archive', body: 'Durable deliverables land in Archive with lineage and status. Open supported files with the same in-app viewer used from Chat and Tasks.' },
]

export function CoreTour({ token }: { token: string }) {
  const [open, setOpen] = React.useState(false)
  const dialogRef = React.useRef<HTMLElement>(null)
  const previousFocus = React.useRef<HTMLElement | null>(null)
  const [step, setStep] = React.useState(0)
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => {
    let alive = true
    const replay = () => { setStep(0); setOpen(true) }
    getAlphaSettings(token).then(settings => { if (alive && !settings.tour_core_done) setOpen(true) }).catch(() => undefined)
    window.addEventListener('proxima:tour-core', replay)
    return () => { alive = false; window.removeEventListener('proxima:tour-core', replay) }
  }, [token])
  const finish = React.useCallback(async () => {
    if (busy) return
    setBusy(true)
    setOpen(false)
    try { await saveAlphaSettings(token, { tour_core_done: true }) }
    catch { /* A failed write replays the tour next time, but never traps the owner now. */ }
    finally { setBusy(false) }
  }, [busy, token])
  React.useEffect(() => {
    if (!open) return
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = window.requestAnimationFrame(() => dialogRef.current?.focus())
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation(); void finish(); return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const controls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'))
      if (!controls.length) { event.preventDefault(); return }
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', key, true)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('keydown', key, true)
      previousFocus.current?.focus()
    }
  }, [open, finish])
  if (!open) return null
  const current = STEPS[step]
  return <div className="tour-scrim" role="presentation">
    <section ref={dialogRef} className="core-tour" role="dialog" aria-modal="true" aria-labelledby="core-tour-title" tabIndex={-1}>
      <div className="tour-progress" aria-label={`Step ${step + 1} of ${STEPS.length}`}>{STEPS.map((_, index) => <i className={index <= step ? 'active' : ''} key={index} />)}</div>
      <span className="eyebrow">{current.eyebrow}</span>
      <h2 id="core-tour-title">{current.title}</h2>
      <p>{current.body}</p>
      <div className="tour-actions"><button type="button" className="text-button" disabled={busy} onClick={() => void finish()}>{busy ? 'Saving…' : 'Skip tour'}</button><div>{step > 0 && <button type="button" className="ghost-button" disabled={busy} onClick={() => setStep(value => value - 1)}>Back</button>}<button type="button" className="primary-button" disabled={busy} onClick={() => step === STEPS.length - 1 ? void finish() : setStep(value => value + 1)}>{step === STEPS.length - 1 ? 'Start using Proxima' : 'Next'}</button></div></div>
    </section>
  </div>
}
