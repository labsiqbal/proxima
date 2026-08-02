import React from 'react'
import { IconClose } from '../shell/icons'

export type ToastTone = 'info' | 'success' | 'warning' | 'danger'

/**
 * One toast card. The single visual language for transient shell notices -
 * Master run events and the global error surface both render through it, so
 * there is exactly one toast look to maintain.
 */
export function ToastCard(props: {
  tone: ToastTone
  title: string
  body?: React.ReactNode
  priority?: 'polite' | 'assertive'
  dismissLabel: string
  onDismiss: () => void
  children?: React.ReactNode
}) {
  const priority = props.priority ?? 'polite'
  return (
    <div
      className={`toast ${props.tone}`}
      role={priority === 'assertive' ? 'alert' : 'status'}
      aria-live={priority}
      aria-atomic="true"
    >
      <div className="toast-content">
        <strong>{props.title}</strong>
        {props.body != null && <small>{props.body}</small>}
        {props.children}
      </div>
      <button
        type="button"
        className="icon-button"
        aria-label={props.dismissLabel}
        onClick={props.onDismiss}
      >
        <IconClose size={15} />
      </button>
    </div>
  )
}
