import React from 'react'
import { useMasterState } from '../../master/MasterStateProvider'
import { MasterComposer } from './MasterComposer'
import { MasterConversation } from './MasterConversation'
import { MasterPendingFocus } from './MasterTargetPicker'
import {
  IconChevronLeft,
  IconChevronRight,
  IconClose,
  IconHome,
  IconSparkle,
} from '../shell/icons'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

function focusableIn(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(element => {
    const style = window.getComputedStyle(element)
    return style.display !== 'none'
      && style.visibility !== 'hidden'
      && element.getAttribute('aria-hidden') !== 'true'
  })
}

export function MasterPopup({
  token,
  available,
  onOpenHome,
  onOpenJob,
}: {
  token: string
  available: boolean
  onOpenHome: () => void
  onOpenJob: (id: number, engine?: string) => void
}) {
  const { enabled, desk, connection, unread, popup, focus, fleet, actions } =
    useMasterState()
  const {
    closePopup,
    openPopup,
    setPopupCorner,
    togglePopup,
  } = actions
  const triggerRef = React.useRef<HTMLButtonElement>(null)
  const dialogRef = React.useRef<HTMLElement>(null)
  const wasOpenRef = React.useRef(false)
  const visible = enabled && available

  React.useEffect(() => {
    if (!visible && popup.open) closePopup()
  }, [closePopup, popup.open, visible])

  React.useEffect(() => {
    if (!visible) return
    const shortcut = (event: KeyboardEvent) => {
      if (
        (event.ctrlKey || event.metaKey)
        && event.shiftKey
        && event.key.toLowerCase() === 'm'
      ) {
        event.preventDefault()
        togglePopup()
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [togglePopup, visible])

  React.useEffect(() => {
    if (!popup.open) {
      if (wasOpenRef.current) {
        wasOpenRef.current = false
        const trigger = triggerRef.current
        if (trigger?.isConnected) window.requestAnimationFrame(() => trigger.focus())
      }
      return
    }
    wasOpenRef.current = true
    const frame = window.requestAnimationFrame(() => {
      const textarea = dialogRef.current?.querySelector<HTMLElement>(
        'textarea[aria-label="Message Master"]',
      )
      ;(textarea || focusableIn(dialogRef.current)[0])?.focus()
    })
    const trap = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closePopup()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusableIn(dialogRef.current)
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialogRef.current?.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !dialogRef.current?.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', trap)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('keydown', trap)
    }
  }, [closePopup, popup.open])

  if (!visible) return null

  const focusedContainer = fleet.containers.find(
    container => container.id === focus.containerId,
  )
  const focusLabel = focus.mode === 'container'
    ? focusedContainer?.identity_label || focusedContainer?.name || 'Focused Container'
    : 'Fleet'
  const cornerLabel = popup.preferredCorner === 'right'
    ? 'Move popup to bottom left'
    : 'Move popup to bottom right'

  return (
    <div className={`master-popup-root corner-${popup.preferredCorner}`}>
      {!popup.open && (
        <button
          ref={triggerRef}
          type="button"
          className="master-popup-trigger"
          aria-label="Open Master popup"
          aria-haspopup="dialog"
          aria-expanded="false"
          title="Open Master popup (Ctrl or Command + Shift + M)"
          onClick={openPopup}
        >
          <IconSparkle size={19} />
          {unread.count > 0 && (
            <b aria-label={`${unread.count} unread Master messages`}>
              {unread.count > 99 ? '99+' : unread.count}
            </b>
          )}
        </button>
      )}
      {popup.open && (
        <>
          <button
            type="button"
            className="master-popup-scrim"
            aria-label="Close Master popup"
            onClick={closePopup}
          />
          <section
            ref={dialogRef}
            className="master-popup"
            role="dialog"
            aria-modal="true"
            aria-labelledby="master-popup-title"
          >
            <header className="master-popup-head">
              <div>
                <span className="master-popup-mark" aria-hidden="true">
                  <IconSparkle size={16} />
                </span>
                <span>
                  <strong id="master-popup-title">Master</strong>
                  <small>
                    {focusLabel}
                    {' · '}
                    {connection.state === 'connected' ? 'Live' : 'Reconnecting'}
                  </small>
                  <MasterPendingFocus />
                </span>
              </div>
              <nav aria-label="Master popup actions">
                <button
                  type="button"
                  className="icon-button"
                  aria-label={cornerLabel}
                  title={cornerLabel}
                  onClick={() => setPopupCorner(
                    popup.preferredCorner === 'right' ? 'left' : 'right',
                  )}
                >
                  {popup.preferredCorner === 'right'
                    ? <IconChevronLeft size={16} />
                    : <IconChevronRight size={16} />}
                </button>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Open full Master home"
                  title="Open full Master home"
                  onClick={() => {
                    closePopup()
                    onOpenHome()
                  }}
                >
                  <IconHome size={16} />
                </button>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Close Master popup"
                  title="Close Master popup"
                  onClick={closePopup}
                >
                  <IconClose size={16} />
                </button>
              </nav>
            </header>
            {!desk ? (
              <div className="master-popup-state" role="status">
                <span className="ui-spinner" />
                Opening Master...
              </div>
            ) : (
              <div className="master-popup-conversation">
                <MasterConversation onOpenJob={onOpenJob} />
                <MasterComposer token={token} />
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
