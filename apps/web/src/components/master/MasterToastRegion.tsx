import React from 'react'
import { useMasterState } from '../../master/MasterStateProvider'
import { IconClose } from '../shell/icons'

const TOAST_DURATION_MS = 7000

export function MasterToastRegion({ available = true }: { available?: boolean }) {
  const { popup, toasts, actions } = useMasterState()
  const dismissToast = actions.dismissToast

  React.useEffect(() => {
    const timers = toasts.map(toast => window.setTimeout(
      () => dismissToast(toast.id),
      TOAST_DURATION_MS,
    ))
    return () => timers.forEach(timer => window.clearTimeout(timer))
  }, [dismissToast, toasts])

  if (!available || popup.open || !toasts.length) return null

  return (
    <section className="master-toast-region" aria-label="Master notifications">
      {toasts.map(toast => (
        <div
          className={`master-toast ${toast.tone}`}
          key={toast.id}
          role={toast.priority === 'assertive' ? 'alert' : 'status'}
          aria-live={toast.priority}
          aria-atomic="true"
        >
          <span>
            <strong>{toast.title}</strong>
            <small>{toast.body}</small>
          </span>
          <button
            type="button"
            className="icon-button"
            aria-label={`Dismiss ${toast.title}`}
            onClick={() => dismissToast(toast.id)}
          >
            <IconClose size={15} />
          </button>
        </div>
      ))}
    </section>
  )
}
