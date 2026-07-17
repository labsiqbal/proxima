import React from 'react'

// A file the owner can @-mention. Deliberately narrower than api/files' Artifact —
// mentions need a path to insert and something readable to show, nothing more.
export type MentionItem = { path: string; title?: string; type?: string }

// The "@query" being typed at the caret, if any. Only the tail before the caret is
// considered: a mention is something you are typing now, not text you scrolled past.
export function matchMention(textBeforeCaret: string): { query: string; at: number } | null {
  const match = /(^|[\s([{'"`])@([\w./\\-]*)$/.exec(textBeforeCaret)
  if (!match) return null
  const query = match[2]
  return { query, at: textBeforeCaret.length - query.length - 1 }
}

export function filterMentions(items: MentionItem[], query: string, limit = 8): MentionItem[] {
  const needle = query.toLowerCase()
  return items
    .filter(item => item.path.toLowerCase().includes(needle) || (item.title ?? '').toLowerCase().includes(needle))
    .slice(0, limit)
}

/** Replace the active "@query" with the picked path. The inserted text is the bare
 *  project-relative path — that is what a runner can actually open. */
export function applyMention(text: string, caret: number, at: number, path: string): { text: string; caret: number } {
  const next = `${text.slice(0, at)}${path} ${text.slice(caret)}`
  return { text: next, caret: at + path.length + 1 }
}

/** A textarea where typing @ offers the project's artifacts and inserts the picked
 *  file's path — so instructions and rules can point at real deliverables instead of
 *  describing them. Plain textarea semantics otherwise. */
export function MentionTextarea({ value, onChange, items, rows = 3, placeholder, ariaLabel }: {
  value: string
  onChange: (next: string) => void
  items: MentionItem[]
  rows?: number
  placeholder?: string
  ariaLabel?: string
}) {
  const areaRef = React.useRef<HTMLTextAreaElement | null>(null)
  const [open, setOpen] = React.useState<{ query: string; at: number } | null>(null)
  const [active, setActive] = React.useState(0)
  const matches = open ? filterMentions(items, open.query) : []

  const sync = (element: HTMLTextAreaElement) => {
    const found = matchMention(element.value.slice(0, element.selectionStart ?? element.value.length))
    setOpen(found)
    if (!found) setActive(0)
  }

  const pick = (item: MentionItem) => {
    const element = areaRef.current
    if (!element || !open) return
    const caret = element.selectionStart ?? value.length
    const applied = applyMention(value, caret, open.at, item.path)
    onChange(applied.text)
    setOpen(null)
    requestAnimationFrame(() => {
      element.focus()
      element.setSelectionRange(applied.caret, applied.caret)
    })
  }

  return <div className="mention-wrap">
    <textarea
      ref={areaRef}
      rows={rows}
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
      onChange={event => { onChange(event.target.value); sync(event.target) }}
      onClick={event => sync(event.currentTarget)}
      onKeyDown={event => {
        if (!open || matches.length === 0) return
        if (event.key === 'ArrowDown') { event.preventDefault(); setActive(index => (index + 1) % matches.length) }
        else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(index => (index + matches.length - 1) % matches.length) }
        else if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); pick(matches[Math.min(active, matches.length - 1)]) }
        else if (event.key === 'Escape') setOpen(null)
      }}
      onBlur={() => window.setTimeout(() => setOpen(null), 120)}
    />
    {open && matches.length > 0 && <div className="mention-popover" role="listbox" aria-label="Artifacts">
      {matches.map((item, index) => <button
        type="button"
        key={item.path}
        role="option"
        aria-selected={index === active}
        className={index === active ? 'active' : ''}
        onMouseEnter={() => setActive(index)}
        // onMouseDown beats the textarea blur; onClick would arrive after the popover
        // has already dismissed itself.
        onMouseDown={event => { event.preventDefault(); pick(item) }}
      >
        <strong>{item.title || item.path.split('/').pop()}</strong>
        <span>{item.path}</span>
      </button>)}
    </div>}
  </div>
}
