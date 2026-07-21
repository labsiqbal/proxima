import React from 'react'

// A file the owner can @-mention. Deliberately narrower than api/files' Artifact —
// mentions need a path to insert and something readable to show, nothing more.
export type MentionItem = { path: string; title?: string; type?: string }

const IMAGE_PATH = /\.(?:avif|bmp|gif|jpe?g|png|svg|webp)$/i

/** Turn an explicitly selected image into the same Markdown reference used by file
 * attachments. Media routes can then consume its pixels; ordinary files stay as a
 * project-relative path that project-scoped agents can open from their cwd. */
export function mentionInsertion(item: MentionItem): string {
  if (!IMAGE_PATH.test(item.path) || /[\]\[()\n\r]/.test(item.path)) return item.path
  const name = item.path.split('/').pop() || item.title || 'image'
  return `![${name}](${item.path})`
}

// The "@query" being typed at the caret, if any. Only the tail before the caret is
// considered: a mention is something you are typing now, not text you scrolled past.
export function matchMention(textBeforeCaret: string): { query: string; at: number } | null {
  const match = /(^|[\s([{'"`])@([\w./\\-]*)$/.exec(textBeforeCaret)
  if (!match) return null
  const query = match[2]
  return { query, at: textBeforeCaret.length - query.length - 1 }
}

export function filterMentions(items: MentionItem[], query: string): MentionItem[] {
  const needle = query.toLowerCase()
  if (!needle) return items
  return items
    .map(item => {
      const path = item.path.toLowerCase()
      const name = path.split('/').pop() || path
      const stem = name.replace(/\.[^.]+$/, '')
      const title = (item.title ?? '').toLowerCase()
      const score = name === needle || stem === needle || title === needle ? 0
        : name.startsWith(needle) || title.startsWith(needle) ? 1
          : name.includes(needle) || title.includes(needle) ? 2
            : path.includes(needle) ? 3
              : -1
      return { item, score }
    })
    .filter(match => match.score >= 0)
    .sort((a, b) => a.score - b.score || a.item.path.length - b.item.path.length || a.item.path.localeCompare(b.item.path))
    .map(match => match.item)
}

/** Replace the active "@query" with the picked path. The inserted text is the bare
 *  project-relative path — that is what a runner can actually open. */
export function applyMention(text: string, caret: number, at: number, path: string): { text: string; caret: number } {
  const tail = text.slice(caret)
  const alreadySeparated = /^[\t ]/.test(tail)
  const separator = alreadySeparated ? '' : ' '
  const next = `${text.slice(0, at)}${path}${separator}${tail}`
  // When a separator already existed, place the caret after it so continued typing
  // does not create a doubled space in the middle of a sentence.
  return { text: next, caret: at + path.length + 1 }
}

/** A textarea where typing @ offers the project's files and inserts the picked
 *  reference — so instructions and rules can point at real project context instead
 *  of describing it. Plain textarea semantics otherwise. */
export function MentionTextarea({ value, onChange, items, rows = 3, placeholder, ariaLabel }: {
  value: string
  onChange: (next: string) => void
  items: MentionItem[]
  rows?: number
  placeholder?: string
  ariaLabel?: string
}) {
  const areaRef = React.useRef<HTMLTextAreaElement | null>(null)
  const pendingCaret = React.useRef<{ caret: number; forText: string } | null>(null)
  const listRef = React.useRef<HTMLDivElement | null>(null)
  const listId = React.useId()
  const [open, setOpen] = React.useState<{ query: string; at: number } | null>(null)
  const [active, setActive] = React.useState(0)
  const query = open?.query
  const matches = React.useMemo(
    () => query == null ? [] : filterMentions(items, query),
    [items, query],
  )

  React.useEffect(() => {
    if (!open) return
    listRef.current
      ?.querySelector<HTMLElement>(`[data-mention-index="${active}"]`)
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [active, matches.length, query])

  const sync = (element: HTMLTextAreaElement) => {
    const found = matchMention(element.value.slice(0, element.selectionStart ?? element.value.length))
    setOpen(found)
    setActive(0)
  }

  const pick = (item: MentionItem) => {
    const element = areaRef.current
    if (!element || !open) return
    const caret = element.selectionStart ?? value.length
    const applied = applyMention(value, caret, open.at, mentionInsertion(item))
    pendingCaret.current = { caret: applied.caret, forText: applied.text }
    onChange(applied.text)
    setOpen(null)
  }

  // Place the caret after the inserted mention once the controlled re-render has
  // committed the new value — but only while the value is still exactly the inserted
  // text. If more keystrokes landed first, restoring the stale caret would scramble
  // their order (the old requestAnimationFrame version did exactly that).
  React.useLayoutEffect(() => {
    const pending = pendingCaret.current
    const element = areaRef.current
    if (!pending || !element) return
    pendingCaret.current = null
    if (element.value !== pending.forText) return
    element.focus()
    element.setSelectionRange(pending.caret, pending.caret)
  })

  return <div className="mention-wrap">
    <textarea
      ref={areaRef}
      rows={rows}
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
      aria-autocomplete="list"
      aria-expanded={open != null && matches.length > 0}
      aria-controls={open && matches.length > 0 ? listId : undefined}
      aria-activedescendant={open && matches.length > 0 ? `${listId}-option-${Math.min(active, matches.length - 1)}` : undefined}
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
    {open && matches.length > 0 && <div id={listId} ref={listRef} className="mention-popover" role="listbox" aria-label="Project files">
      {matches.map((item, index) => <button
        type="button"
        key={item.path}
        id={`${listId}-option-${index}`}
        data-mention-index={index}
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
