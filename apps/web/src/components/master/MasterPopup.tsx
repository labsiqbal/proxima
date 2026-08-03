import React from 'react'
import { useMasterState } from '../../master/MasterStateProvider'
import { MasterComposer } from './MasterComposer'
import { MasterConversation } from './MasterConversation'
import { MasterPendingFocus } from './MasterTargetPicker'
import {
  areaDisplayLabel,
  projectSecondaryContext,
} from './projectContext'
import {
  IconChevronLeft,
  IconChevronRight,
  IconClose,
  IconHome,
  IconSparkle,
} from '../shell/icons'
import {
  COMPOSER_DOCK_SELECTOR,
  masterTriggerClearance,
} from './triggerClearance'

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

/**
 * Keep the resting trigger above whatever composer the current surface docks at
 * the bottom (#154). Only runs while the trigger is on screen: an open popup
 * covers the surface anyway, and its panel is anchored to the same root.
 */
function useComposerClearance(
  active: boolean,
  triggerRef: React.RefObject<HTMLButtonElement | null>,
): number {
  const [clearance, setClearance] = React.useState(0)
  React.useEffect(() => {
    if (!active || typeof window === 'undefined') {
      setClearance(0)
      return
    }
    let timer: ReturnType<typeof setTimeout> | undefined
    const measure = () => {
      const trigger = triggerRef.current
      const docks = [...document.querySelectorAll<HTMLElement>(COMPOSER_DOCK_SELECTOR)]
      setClearance(masterTriggerClearance({
        trigger: trigger ? trigger.getBoundingClientRect() : null,
        docks: docks.map(dock => dock.getBoundingClientRect()),
        viewportHeight: window.innerHeight,
      }))
    }
    // Trailing debounce: a composer that grows as you type, a surface swap, or a
    // rotation all settle within a frame or two, and the trigger moving a beat
    // later is imperceptible — measuring on every mutation is not.
    const schedule = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(measure, 100)
    }
    measure()
    window.addEventListener('resize', schedule)
    const observers: { disconnect: () => void }[] = []
    if (typeof ResizeObserver !== 'undefined') {
      const resize = new ResizeObserver(schedule)
      resize.observe(document.documentElement)
      observers.push(resize)
    }
    if (typeof MutationObserver !== 'undefined') {
      const mutation = new MutationObserver(schedule)
      mutation.observe(document.body, { childList: true, subtree: true })
      observers.push(mutation)
    }
    return () => {
      if (timer) clearTimeout(timer)
      window.removeEventListener('resize', schedule)
      for (const observer of observers) observer.disconnect()
    }
  }, [active, triggerRef])
  return clearance
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
  const { enabled, desk, connection, unread, popup, focus, target, fleet, actions } =
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
  const clearance = useComposerClearance(visible && !popup.open, triggerRef)

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
        'textarea[aria-label="Message Master"]:not([disabled])',
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
  const targetContainer = fleet.containers.find(
    container => container.id === target.containerId,
  )
  const contextProject = target.mode === 'explicit' ? targetContainer : focusedContainer
  const contextAreas = contextProject
    ? fleet.areasByContainer[contextProject.id]
    : null
  const contextPrimary = contextProject?.name
    || (focus.mode === 'container' ? 'Focused Project' : 'Fleet')
  const contextSecondary = contextProject
    ? projectSecondaryContext(
        contextProject,
        areaDisplayLabel(contextAreas, target.mode === 'explicit' ? target.areaId : null),
      )
    : 'Project and Area chosen by Master'
  const cornerLabel = popup.preferredCorner === 'right'
    ? 'Move popup to bottom left'
    : 'Move popup to bottom right'

  return (
    <div
      className={`master-popup-root corner-${popup.preferredCorner} ${popup.open ? 'is-open' : 'is-resting'}`}
      style={{ ['--master-popup-clearance']: `${clearance}px` } as React.CSSProperties}
    >
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
                  <strong className="master-popup-project">{contextPrimary}</strong>
                  <small className="master-popup-context">{contextSecondary}</small>
                  <small>{connection.state === 'connected' ? 'Live' : 'Reconnecting'}</small>
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
