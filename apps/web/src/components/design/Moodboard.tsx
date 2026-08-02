import React from 'react'
import {
  addMoodboardItem,
  deleteMoodboardItem,
  fileUrl,
  isSvgPath,
  listMoodboard,
  updateMoodboardItem,
  uploadFile,
  type MoodboardItem,
} from '../../api/files'
import { useProjectAreaPaths } from '../../hooks/useProjectAreaPaths'
import { useRawBlobUrl } from '../../hooks/useRawBlobUrl'
import { confirmDialog } from '../ui/Dialog'

export function DesignStudioMenu({ active, onCanvas, onBrandGuide, onMoodboard }: {
  active: 'canvas' | 'brand-guide' | 'moodboard'
  onCanvas: () => void
  onBrandGuide: () => void
  onMoodboard: () => void
}) {
  return <nav className="ds-section-menu" aria-label="Design Studio">
    <button type="button" className={active === 'canvas' ? 'active' : ''} aria-current={active === 'canvas' ? 'page' : undefined} onClick={onCanvas}>Canvas</button>
    <button type="button" className={active === 'brand-guide' ? 'active' : ''} aria-current={active === 'brand-guide' ? 'page' : undefined} onClick={onBrandGuide}>Brand Guide</button>
    <button type="button" className={active === 'moodboard' ? 'active' : ''} aria-current={active === 'moodboard' ? 'page' : undefined} onClick={onMoodboard}>Moodboard</button>
  </nav>
}

const splitTags = (value: string) => value
  .split(/[\s,]+/)
  .map(tag => tag.trim().replace(/^#/, '').toLowerCase())
  .filter(Boolean)
  .filter((tag, index, all) => all.indexOf(tag) === index)
  .slice(0, 12)

function MoodboardShotImage({
  token,
  slug,
  path,
  alt,
}: {
  token: string
  slug: string
  path: string
  alt: string
}) {
  const svg = isSvgPath(path)
  const blob = useRawBlobUrl(svg ? token : undefined, svg ? slug : undefined, svg ? path : '')
  if (svg) {
    if (blob.status === 'error') {
      return <div className="moodboard-shot-fallback" role="img" aria-label={`${alt} failed to load`}>
        <span aria-hidden="true">!</span>
        <small>Preview unavailable</small>
        <button type="button" className="ghost-button sm" onClick={blob.retry}>Retry</button>
      </div>
    }
    if (blob.status !== 'ready' || !blob.url) {
      return <div className="moodboard-shot-fallback muted" aria-busy="true">
        <small>Loading…</small>
      </div>
    }
    return <img src={blob.url} alt={alt} />
  }
  return <img src={fileUrl(slug, path)} alt={alt} />
}

function MoodboardEdit({ item, busy, onClose, onSave }: {
  item: MoodboardItem
  busy: boolean
  onClose: () => void
  onSave: (note: string, tags: string[]) => void
}) {
  const [note, setNote] = React.useState(item.note)
  const [tags, setTags] = React.useState(item.tags.join(' '))
  return <div className="modal-scrim" onClick={onClose}>
    <form className="modal-card moodboard-edit" role="dialog" aria-modal="true" aria-labelledby="moodboard-edit-title" onClick={event => event.stopPropagation()} onSubmit={event => { event.preventDefault(); onSave(note, splitTags(tags)) }}>
      <div className="moodboard-dialog-head">
        <strong id="moodboard-edit-title">Edit reference</strong>
        <button type="button" className="ghost-button" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      <label>Note<textarea name="note" rows={5} value={note} placeholder="What should the design agent draw from this?" onChange={event => setNote(event.target.value)} /></label>
      <label>Tags<input name="tags" value={tags} placeholder="hero dark saas" onChange={event => setTags(event.target.value)} /></label>
      <p className="muted moodboard-field-hint">Separate tags with spaces or commas.</p>
      <div className="moodboard-dialog-actions">
        <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>
        <button type="submit" className="primary-button" disabled={busy}>{busy ? 'Saving...' : 'Save'}</button>
      </div>
    </form>
  </div>
}

export function Moodboard({ token, slug }: { token: string; slug: string }) {
  const [items, setItems] = React.useState<MoodboardItem[]>([])
  const [loading, setLoading] = React.useState(true)
  const [search, setSearch] = React.useState('')
  const [tag, setTag] = React.useState('')
  const [addOpen, setAddOpen] = React.useState(false)
  const [link, setLink] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [status, setStatus] = React.useState('')
  const [edit, setEdit] = React.useState<MoodboardItem | null>(null)
  const fileRef = React.useRef<HTMLInputElement>(null)
  // The moodboard lives in the project's mapped artifacts folder (layout
  // map, prune #138) - the upload dir must name that real path.
  const areaPaths = useProjectAreaPaths(token, slug)

  const load = React.useCallback(async () => {
    setLoading(true)
    try {
      const result = await listMoodboard(token, slug)
      setItems(result.items)
      setStatus('')
    } catch (error) {
      setStatus(`Could not load the Moodboard: ${String(error)}`)
    } finally {
      setLoading(false)
    }
  }, [token, slug])

  React.useEffect(() => { void load() }, [load])

  const add = async (body: Parameters<typeof addMoodboardItem>[2]) => {
    if (busy) return
    setBusy(true)
    setStatus(body.url ? 'Fetching link preview...' : 'Adding screenshot...')
    try {
      const result = await addMoodboardItem(token, slug, body)
      setItems(current => [result.item, ...current])
      setLink('')
      setAddOpen(false)
      setStatus(result.warning || 'Reference added.')
    } catch (error) {
      setStatus(`Could not add reference: ${String(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const addFiles = async (files: File[]) => {
    const images = files.filter(file => file.type.startsWith('image/')).slice(0, 6)
    if (!images.length || busy || !areaPaths) return
    setBusy(true)
    setStatus(`Uploading ${images.length} screenshot${images.length === 1 ? '' : 's'}...`)
    try {
      const added: MoodboardItem[] = []
      for (const file of images) {
        const uploaded = await uploadFile(token, slug, file, `${areaPaths.artifacts}/moodboard/images`)
        const result = await addMoodboardItem(token, slug, {
          imagePath: uploaded.path,
          title: file.name.replace(/\.[^.]+$/, ''),
          siteName: 'Uploaded screenshot',
        })
        added.push(result.item)
      }
      setItems(current => [...added.reverse(), ...current])
      setAddOpen(false)
      setStatus(`${images.length} screenshot${images.length === 1 ? '' : 's'} added.`)
    } catch (error) {
      setStatus(`Could not upload screenshot: ${String(error)}`)
      void load()
    } finally {
      setBusy(false)
    }
  }

  const patch = async (item: MoodboardItem, body: Parameters<typeof updateMoodboardItem>[3]) => {
    setBusy(true)
    try {
      const result = await updateMoodboardItem(token, slug, item.id, body)
      setItems(current => current.map(candidate => candidate.id === item.id ? result.item : candidate))
      setEdit(null)
      setStatus(result.item.useAsReference ? 'Reference selected for design runs.' : 'Reference updated.')
    } catch (error) {
      setStatus(`Could not update reference: ${String(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (item: MoodboardItem) => {
    const confirmed = await confirmDialog({
      title: 'Delete reference?',
      message: 'This removes the card and its Moodboard image. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!confirmed) return
    try {
      await deleteMoodboardItem(token, slug, item.id)
      setItems(current => current.filter(candidate => candidate.id !== item.id))
      setStatus('Reference deleted.')
    } catch (error) {
      setStatus(`Could not delete reference: ${String(error)}`)
    }
  }

  const allTags = [...new Set(items.flatMap(item => item.tags))].sort()
  const query = search.trim().toLowerCase()
  const filtered = items.filter(item => {
    if (tag && !item.tags.includes(tag)) return false
    if (!query) return true
    return [item.title, item.siteName, item.url || '', item.note, ...item.tags].some(value => value.toLowerCase().includes(query))
  })

  return <div
    className="moodboard"
    onDragOver={event => { if ([...event.dataTransfer.items].some(item => item.kind === 'file')) event.preventDefault() }}
    onDrop={event => { event.preventDefault(); void addFiles([...event.dataTransfer.files]) }}
    onPaste={event => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return
      const files = [...event.clipboardData.files]
      if (files.length) {
        event.preventDefault()
        void addFiles(files)
        return
      }
      const text = event.clipboardData.getData('text').trim()
      if (/^https?:\/\//i.test(text)) {
        event.preventDefault()
        void add({ url: text })
      }
    }}
  >
    <div className="moodboard-toolbar">
      <div>
        <h2>Moodboard</h2>
        <p>Curated inspiration for this project.</p>
      </div>
      <label className="moodboard-search">
        <span aria-hidden="true">⌕</span>
        <input type="search" name="search" value={search} placeholder="Search references" aria-label="Search references" onChange={event => setSearch(event.target.value)} />
      </label>
      <div className="moodboard-tag-filter" aria-label="Filter by tag">
        {allTags.map(value => <button type="button" key={value} className={tag === value ? 'active' : ''} aria-pressed={tag === value} onClick={() => setTag(current => current === value ? '' : value)}>#{value}</button>)}
      </div>
      <div className="moodboard-add-wrap">
        <button type="button" className="primary-button" aria-expanded={addOpen} onClick={() => setAddOpen(open => !open)}>+ Add</button>
        {addOpen && <div className="moodboard-add-popover">
          <strong>Add reference</strong>
          <div className="moodboard-add-method featured">
            <div className="moodboard-add-method-title"><span>🔗 Paste link</span><small>Recommended</small></div>
            <p>We fetch the link preview image automatically.</p>
            <form onSubmit={event => { event.preventDefault(); if (link.trim()) void add({ url: link.trim() }) }}>
              <input type="url" name="url" value={link} autoFocus placeholder="https://linear.app" aria-label="Reference URL" onChange={event => setLink(event.target.value)} onPaste={event => {
                const value = event.clipboardData.getData('text').trim()
                if (/^https?:\/\//i.test(value)) {
                  event.preventDefault()
                  setLink(value)
                  void add({ url: value })
                }
              }} />
              <button type="submit" className="ghost-button" disabled={!link.trim() || busy}>Add</button>
            </form>
          </div>
          <button type="button" className="moodboard-add-method plain" onClick={() => fileRef.current?.click()}>
            <span>🖼 Upload screenshot</span>
            <small>Pick a PNG, JPG, WebP, GIF, SVG, or AVIF.</small>
          </button>
          <div className="moodboard-add-method plain" aria-hidden="true">
            <span>⇩ Drag or paste</span>
            <small>Drop image files anywhere on the board.</small>
          </div>
        </div>}
      </div>
      <input ref={fileRef} type="file" name="screenshots" accept="image/*" multiple hidden onChange={event => { void addFiles([...(event.target.files || [])]); event.currentTarget.value = '' }} />
    </div>
    {status && <p className="moodboard-status" role="status">{status}</p>}
    {tag && <button type="button" className="moodboard-clear-filter" onClick={() => setTag('')}>Showing #{tag} <span>Clear</span></button>}
    {loading
      ? <div className="moodboard-empty muted">Loading references...</div>
      : filtered.length === 0
        ? <div className="moodboard-empty"><strong>{items.length ? 'No references match.' : 'Collect the visual direction.'}</strong><p className="muted">{items.length ? 'Try another search or clear the tag filter.' : 'Paste a link, upload a screenshot, or drag an image onto this board.'}</p>{!items.length && <button type="button" className="primary-button" onClick={() => setAddOpen(true)}>+ Add first reference</button>}</div>
        : <div className="moodboard-grid">{filtered.map(item => <article className={`moodboard-card ${item.useAsReference ? 'selected-reference' : ''}`} key={item.id}>
            <div className="moodboard-shot">
              {item.imagePath
                ? <MoodboardShotImage
                    token={token}
                    slug={slug}
                    path={item.imagePath}
                    alt={item.title || `Reference from ${item.siteName}`}
                  />
                : <div className="moodboard-shot-fallback" aria-label="Preview unavailable"><span>{(item.siteName || 'R').slice(0, 1).toUpperCase()}</span><small>Preview unavailable</small></div>}
              <div className="moodboard-actions">
                {item.url && <a href={item.url} target="_blank" rel="noreferrer" title="Open link" aria-label={`Open ${item.siteName}`}>↗</a>}
                <button type="button" title="Edit note and tags" aria-label={`Edit ${item.siteName}`} onClick={() => setEdit(item)}>✎</button>
                <button type="button" className="use" title={item.useAsReference ? 'Remove from design references' : 'Use as reference'} aria-label={`${item.useAsReference ? 'Stop using' : 'Use'} ${item.siteName} as reference`} aria-pressed={item.useAsReference} disabled={busy} onClick={() => void patch(item, { useAsReference: !item.useAsReference })}>★</button>
                <button type="button" className="delete" title="Delete" aria-label={`Delete ${item.siteName}`} onClick={() => void remove(item)}>⌫</button>
              </div>
              {item.useAsReference && <span className="moodboard-reference-badge">Used in design</span>}
            </div>
            <div className="moodboard-card-body">
              <div className="moodboard-site">
                <span className="moodboard-favicon-fallback" aria-hidden="true">{item.siteName.slice(0, 1).toUpperCase()}</span>
                {item.faviconUrl && <img src={item.faviconUrl} alt="" />}
                <span>{item.siteName}</span>
              </div>
              {item.title && item.title !== item.siteName && <strong className="moodboard-title">{item.title}</strong>}
              {item.note && <p className="moodboard-note">{item.note}</p>}
              {item.tags.length > 0 && <div className="moodboard-tags">{item.tags.map(value => <button type="button" key={value} onClick={() => setTag(value)}>#{value}</button>)}</div>}
            </div>
          </article>)}</div>}
    {edit && <MoodboardEdit item={edit} busy={busy} onClose={() => setEdit(null)} onSave={(note, tags) => void patch(edit, { note, tags })} />}
  </div>
}
