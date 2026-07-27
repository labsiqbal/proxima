import React from 'react'

/**
 * Shared empty-state grammar (UI Flow Q5):
 * title + what-you-can-do + short tutorial steps + one primary CTA.
 */
export type TeachingEmptyProps = {
  title: string
  /** Short explanation of the surface purpose. */
  lead: string
  /** Main capabilities on this surface (bullets). */
  capabilities?: string[]
  /** Ordered tutorial callouts (e.g. “Send a message…”, “Watch Tasks…”). */
  steps?: string[]
  /** Optional single primary action. */
  ctaLabel?: string
  onCta?: () => void
  /** Extra content under the tutorial (e.g. Master example chips). */
  children?: React.ReactNode
  className?: string
  /** Optional mark / icon above the title. */
  mark?: React.ReactNode
}

export function TeachingEmpty({
  title,
  lead,
  capabilities,
  steps,
  ctaLabel,
  onCta,
  children,
  className,
  mark,
}: TeachingEmptyProps) {
  return (
    <div className={['teaching-empty', className].filter(Boolean).join(' ')} data-testid="teaching-empty">
      {mark ? <div className="teaching-empty-mark" aria-hidden="true">{mark}</div> : null}
      <h3 className="teaching-empty-title">{title}</h3>
      <p className="teaching-empty-lead">{lead}</p>
      {capabilities && capabilities.length > 0 && (
        <ul className="teaching-empty-caps" aria-label="What you can do here">
          {capabilities.map(item => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      {steps && steps.length > 0 && (
        <ol className="teaching-empty-steps" aria-label="Getting started">
          {steps.map((step, index) => (
            <li key={step}>
              <span className="teaching-empty-step-n" aria-hidden="true">{index + 1}</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      )}
      {ctaLabel && onCta ? (
        <button type="button" className="primary-button teaching-empty-cta" onClick={onCta}>
          {ctaLabel}
        </button>
      ) : null}
      {children}
    </div>
  )
}
