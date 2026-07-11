import type React from 'react'

export function ProximaMark({ className = '', label }: { className?: string; label?: string }) {
  return <span className={`proxima-mark ${className}`} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
    <svg viewBox="0 0 64 64" focusable="false">
      <path className="proxima-mark-orbit" d="M12 34c2-14 12-24 25-24 6 0 11 2 15 5" />
      <path className="proxima-mark-letter" d="M20 48V17h14c9 0 15 5 15 13s-6 13-15 13H28" />
      <path className="proxima-mark-star" d="M49 8l1.8 5.2L56 15l-5.2 1.8L49 22l-1.8-5.2L42 15l5.2-1.8L49 8z" />
    </svg>
  </span>
}
