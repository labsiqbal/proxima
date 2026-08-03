import React from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { EditorView } from '@codemirror/view'
import { markdown } from '@codemirror/lang-markdown'
import { oneDark } from '@codemirror/theme-one-dark'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { FsAdapter } from '../../api/fsAdapter'
import type { FileTarget } from '../../types'
import { confirmDialog } from '../ui/Dialog'
import { baseName, linkifyWiki } from './wikiGraph'

/**
 * The wiki graph a note lives in: its backlinks and how a `[[wikilink]]`
 * resolves. Omit it and this is a plain markdown document editor - the shape
 * Artifacts opens a document in (#146), where there is no graph to consult.
 */
export type WikiNoteGraph = {
  backlinks: string[]
  resolve: (name: string) => string | null
  onOpenNote: (path: string) => void
  /** Create-and-open a missing [[wikilink]] target (Obsidian-style). */
  onCreateNote?: (target: string) => void
}

export function WikiNote({ fs, path, target, wiki, onClose, closeLabel = 'Close', closeTitle, closeAs = 'close', onSaved, autoFocus = false, defaultMode = 'preview' }: {
  fs: FsAdapter
  path: string
  /** Area-mapped ref when the document is not plain container-relative (#136/#138). */
  target?: FileTarget
  wiki?: WikiNoteGraph
  onClose: () => void
  /** The close control doubles as the way back out of a main-window editor. */
  closeLabel?: string
  closeTitle?: string
  /**
   * What that control IS, which decides where it sits (#159). A `close`
   * dismisses a pane and stays with the other actions on the right; a `back`
   * leaves a main-window surface, so it leads the row like every other back
   * affordance in the app (chrome Back, `BackButton`, the artifact viewer's
   * `← Gallery`).
   */
  closeAs?: 'close' | 'back'
  onSaved?: () => void
  /** Put the caret in the document on mount - the editor IS the surface. */
  autoFocus?: boolean
  defaultMode?: 'edit' | 'preview'
}) {
  const [content, setContent] = React.useState('')
  const [dirty, setDirty] = React.useState(false)
  const [mode, setMode] = React.useState<'edit' | 'preview'>(defaultMode)
  const [status, setStatus] = React.useState('loading')
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
  const requestSeq = React.useRef(0)
  const editVersion = React.useRef(0)
  const mountedRef = React.useRef(true)
  const ref = target || path

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    const seq = ++requestSeq.current
    setStatus('loading'); setDirty(false); setMode(defaultMode)
    fs.read(ref)
      .then(b => {
        if (!mountedRef.current || seq !== requestSeq.current) return
        setContent(b.content)
        setStatus('ready')
      })
      .catch(e => {
        if (mountedRef.current && seq === requestSeq.current) setStatus(String(e))
      })
  }, [fs, ref, defaultMode])

  const save = async () => {
    if (status === 'loading' || status === 'saving') return
    const seq = ++requestSeq.current
    const savedEditVersion = editVersion.current
    setStatus('saving')
    try {
      await fs.write(ref, content)
      if (!mountedRef.current || seq !== requestSeq.current || editVersion.current !== savedEditVersion) return
      setDirty(false)
      setStatus('saved')
      onSaved?.()
    } catch (e) {
      if (mountedRef.current && seq === requestSeq.current) setStatus(String(e))
    }
  }

  // Leaving with unsaved bytes is a real loss now that this editor holds
  // ordinary project files, not only wiki notes the graph would surface again.
  const requestClose = async () => {
    if (status === 'saving') return
    if (dirty) {
      const discard = await confirmDialog({
        title: 'Discard unsaved edits?',
        message: 'Your unsaved edits to this document will be lost.',
        confirmLabel: 'Discard',
        danger: true,
      })
      if (!discard) return
    }
    onClose()
  }

  const relativeWikiPath = (href?: string | null) => {
    const rawHref = String(href || '').trim()
    if (!rawHref || /^(https?:|mailto:|tel:|data:|blob:|#)/i.test(rawHref)) return null
    const clean = decodeURIComponent(rawHref.split('#')[0].split('?')[0]).replace(/^\/+/, '').replace(/^wiki\//, '')
    if (!/\.(md|markdown)$/i.test(clean)) return null
    const base = path.split('/').slice(0, -1).join('/')
    const raw = rawHref.startsWith('/') || rawHref.startsWith('wiki/') ? clean : (base ? `${base}/${clean}` : clean)
    const parts: string[] = []
    for (const part of raw.split('/')) {
      if (!part || part === '.') continue
      if (part === '..') parts.pop()
      else parts.push(part)
    }
    return parts.join('/')
  }

  const closeButton = <button
    className={`ghost-button${closeAs === 'back' ? ' wiki-note-back' : ''}`}
    onClick={() => void requestClose()}
    disabled={status === 'saving'}
    title={closeTitle}
    aria-label={closeTitle}
  >{closeLabel}</button>

  return <div className="wiki-note">
    <div className="wiki-note-head">
      {closeAs === 'back' && closeButton}
      <strong title={path}>{baseName(path)}{dirty ? ' •' : ''}</strong>
      <div className="seg sm"><button className={mode === 'edit' ? 'active' : ''} onClick={() => setMode('edit')}>Edit</button><button className={mode === 'preview' ? 'active' : ''} onClick={() => setMode('preview')}>Preview</button></div>
      <div><button className="ghost-button" onClick={() => void save()} disabled={status === 'loading' || status === 'saving'}>{status === 'saving' ? 'Saving…' : 'Save'}</button>{closeAs === 'close' && closeButton}</div>
    </div>
    {status === 'loading'
      ? <p className="muted wiki-note-loading">Loading…</p>
      : mode === 'edit'
        ? <div className="cm-wrap"><CodeMirror value={content} height="100%" theme={isDark ? oneDark : 'light'} extensions={[markdown(), EditorView.lineWrapping]} autoFocus={autoFocus} onChange={v => { editVersion.current += 1; setContent(v); setDirty(true); if (status === 'saved') setStatus('ready') }} basicSetup={{ lineNumbers: true, highlightActiveLine: true }} /></div>
        : <div className="wiki-preview md"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
            a: ({ href, children }) => {
              if (wiki && href && href.startsWith('#wiki:')) {
                const linkTarget = decodeURIComponent(href.slice(6))
                const tp = wiki.resolve(linkTarget)
                const label = String(typeof children === 'string' ? children : linkTarget).trim() || linkTarget.trim()
                return <a
                  className={`wikilink ${tp ? '' : 'missing'}`}
                  href="#"
                  title={tp ? `Open ${baseName(tp)}` : `Create note “${label}”`}
                  onClick={e => {
                    e.preventDefault()
                    if (tp) wiki.onOpenNote(tp)
                    else wiki.onCreateNote?.(linkTarget)
                  }}
                >{children}</a>
              }
              const tp = wiki ? relativeWikiPath(href) : null
              if (tp) return <a className="wikilink" href="#" title={`Open ${baseName(tp)}`} onClick={e => { e.preventDefault(); wiki?.onOpenNote(tp) }}>{children}</a>
              return <a href={href} target="_blank" rel="noreferrer">{children}</a>
            }
          }}>{wiki ? linkifyWiki(content) : content}</ReactMarkdown></div>}
    {wiki && <div className="wiki-backlinks">
      <p className="eyebrow">Linked mentions ({wiki.backlinks.length})</p>
      {wiki.backlinks.length === 0 ? <p className="muted">No backlinks yet.</p> : wiki.backlinks.map(b => <button key={b} className="backlink" onClick={() => wiki.onOpenNote(b)}>{baseName(b)}</button>)}
    </div>}
    <div className="file-editor-status muted">{status === 'saved' ? 'Saved' : status === 'saving' ? 'Saving…' : dirty ? 'Unsaved · click Save' : 'Up to date'}</div>
  </div>
}
