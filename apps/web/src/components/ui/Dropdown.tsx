import React from 'react'
import { IconChevronRight } from '../shell/icons'

export type DropdownOption = { value: string; label: string; badge?: string; style?: React.CSSProperties }

// App-wide styled dropdown (matches the Wiki picker). Replaces native <select>
// so the open menu is consistently themed, not the OS default.
export function Dropdown({ value, options, onChange, placeholder, className, disabled, minWidth, dropUp, icon }: {
  value: string
  options: DropdownOption[]
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  minWidth?: number
  dropUp?: boolean
  // Leading glyph inside the trigger, so a dropdown can say what it selects the way
  // the project picker's folder does. Sits in the button, not beside it, to keep the
  // whole control one click target.
  icon?: React.ReactNode
}) {
  const [open, setOpen] = React.useState(false)
  const ref = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const current = options.find(o => o.value === value)
  return <div className={`dd ${className || ''}`} ref={ref}>
    <button type="button" className="dd-btn" disabled={disabled} onClick={() => setOpen(o => !o)} style={minWidth ? { minWidth } : undefined}>
      {icon && <span className="dd-icon">{icon}</span>}
      <span className="dd-label">{current ? current.label : (placeholder || 'Select…')}</span>
      {current?.badge && <span className="dd-badge">{current.badge}</span>}
      <span className="dd-caret"><IconChevronRight size={14} /></span>
    </button>
    {open && !disabled && <div className={`dd-menu ${dropUp ? 'up' : ''}`}>
      {options.map(o => <button type="button" key={o.value} className={`dd-item ${o.value === value ? 'active' : ''}`} onClick={() => { onChange(o.value); setOpen(false) }}>
        <span className="dd-label" style={o.style}>{o.label}</span>{o.badge && <span className="dd-badge">{o.badge}</span>}
      </button>)}
    </div>}
  </div>
}
