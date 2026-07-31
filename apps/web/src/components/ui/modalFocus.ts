import type React from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function modalFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter(element => !element.hidden && element.getAttribute('aria-hidden') !== 'true')
}

export function trapModalTab(event: React.KeyboardEvent<HTMLElement>, container: HTMLElement): void {
  if (event.key !== 'Tab') return
  const focusable = modalFocusableElements(container)
  if (!focusable.length) {
    event.preventDefault()
    container.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const active = document.activeElement
  if (event.shiftKey && (active === first || !container.contains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !container.contains(active))) {
    event.preventDefault()
    first.focus()
  }
}
