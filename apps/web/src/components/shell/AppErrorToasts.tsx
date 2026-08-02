import React from 'react'
import { ToastCard } from '../ui/Toast'
import {
  appErrorSnapshot,
  dismissAppError,
  installGlobalErrorHandlers,
  subscribeAppErrors,
  type AppErrorEntry,
} from '../../lib/errorSurface'

const emptySnapshot: readonly AppErrorEntry[] = []

/**
 * The global error surface. Mounted next to the app root (outside the render
 * error boundary) so it works in every app state — boot, auth gate, delegate
 * mode — and survives a crash of the tree it reports on.
 *
 * Anchored bottom-centre so it never collides with the Master toast column
 * (top-right), the Master popup trigger, or the tool dock.
 */
export function AppErrorToasts({ onReload }: { onReload?: () => void } = {}) {
  React.useEffect(() => installGlobalErrorHandlers(window), [])
  const errors = React.useSyncExternalStore(
    subscribeAppErrors,
    appErrorSnapshot,
    () => emptySnapshot,
  )
  const reload = onReload ?? (() => { window.location.reload() })

  if (!errors.length) return null

  return (
    <section className="toast-region at-bottom-center" aria-label="App errors">
      {errors.map(entry => (
        <ToastCard
          key={entry.id}
          tone="danger"
          priority="assertive"
          title={entry.title}
          body={entry.body}
          dismissLabel={`Dismiss ${entry.title}`}
          onDismiss={() => dismissAppError(entry.id)}
        >
          {entry.count > 1 && <span className="toast-count">{`×${entry.count}`}</span>}
          <details className="toast-detail">
            <summary>Details</summary>
            <pre>{entry.detail}</pre>
          </details>
          {entry.suggestReload && (
            <div className="toast-actions">
              <button type="button" className="primary-button" onClick={reload}>Reload Proxima</button>
            </div>
          )}
        </ToastCard>
      ))}
    </section>
  )
}
