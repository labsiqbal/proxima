// Proxima = the nearest star. The mark is a guiding 4-point star with an orbit
// sweeping behind it and a small satellite spark — a navigation/beacon motif,
// no letterform. Colours/strokes come from the --brand-mark-* tokens via the
// shared .proxima-mark-* classes.
export function ProximaMark({ className = '', label }: { className?: string; label?: string }) {
  return <span className={`proxima-mark ${className}`} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
    <svg viewBox="0 0 64 64" focusable="false">
      <path className="proxima-mark-orbit" d="M15 45A23 23 0 0 1 47 13" />
      <path className="proxima-mark-star" d="M32 15 L36.5 27.5 L49 32 L36.5 36.5 L32 49 L27.5 36.5 L15 32 L27.5 27.5 Z" />
      <circle className="proxima-mark-star" cx="48" cy="13" r="3.1" />
    </svg>
  </span>
}
