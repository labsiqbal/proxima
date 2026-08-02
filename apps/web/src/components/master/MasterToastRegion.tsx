import React from 'react'
import { useMasterState } from '../../master/MasterStateProvider'
import { ToastCard } from '../ui/Toast'

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
    <section className="toast-region at-top-right" aria-label="Master notifications">
      {toasts.map(toast => (
        <ToastCard
          key={toast.id}
          tone={toast.tone}
          priority={toast.priority}
          title={toast.title}
          body={toast.body}
          dismissLabel={`Dismiss ${toast.title}`}
          onDismiss={() => dismissToast(toast.id)}
        />
      ))}
    </section>
  )
}
