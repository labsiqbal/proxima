import React from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { keymap } from '@codemirror/view'
import { oneDark } from '@codemirror/theme-one-dark'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { markdown } from '@codemirror/lang-markdown'
import { json } from '@codemirror/lang-json'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import type { ReadOnlyFsAdapter } from '../../api/fsAdapter'
import type { FileRef } from '../../api/files'
import type { FileTarget } from '../../types'
import { confirmDialog } from '../ui/Dialog'

function langFor(path: string) {
  const ext = path.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'js': case 'jsx': case 'mjs': case 'cjs': return [javascript({ jsx: true })]
    case 'ts': case 'tsx': return [javascript({ jsx: true, typescript: true })]
    case 'py': return [python()]
    case 'md': case 'markdown': return [markdown()]
    case 'json': return [json()]
    case 'html': case 'htm': case 'xml': return [html()]
    case 'css': case 'scss': case 'less': return [css()]
    default: return []
  }
}

export function FileEditor({
  fs,
  write,
  path,
  target,
  onClose,
  closeLabel = 'Close',
  closeTitle,
  autoFocus = false,
  onDirtyChange,
}: {
  fs: ReadOnlyFsAdapter
  write?: (ref: FileRef, content: string) => Promise<unknown>
  path: string
  target?: FileTarget
  onClose: () => void
  /** The close control doubles as the way back out of a main-window editor. */
  closeLabel?: string
  closeTitle?: string
  /** Put the caret in the document on mount - the editor IS the surface. */
  autoFocus?: boolean
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [content, setContent] = React.useState('')
  const [dirty, setDirty] = React.useState(false)
  const [status, setStatus] = React.useState<string>('loading')
  const isDark = typeof document !== 'undefined' && document.documentElement.getAttribute('data-theme') === 'dark'
  const saveRef = React.useRef<() => void>(() => {})
  const requestSeq = React.useRef(0)
  const editVersion = React.useRef(0)
  const mountedRef = React.useRef(true)
  const ref = target || path
  const dirtyRef = React.useRef(false)
  const pathRef = React.useRef(path)
  dirtyRef.current = dirty

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])

  React.useEffect(() => {
    const pathChanged = pathRef.current !== path

    // Keep unsaved project bytes across inspection fs/write swaps and across
    // inspection path browses while write is unavailable. Reload only when the
    // path changes under a writable editor, or when the buffer is clean.
    if (dirtyRef.current && (!pathChanged || !write)) {
      setStatus(write ? 'ready' : 'readonly')
      return
    }

    pathRef.current = path
    const seq = ++requestSeq.current
    setStatus('loading'); setDirty(false)
    fs.read(ref)
      .then(b => {
        if (!mountedRef.current || seq !== requestSeq.current) return
        setContent(b.content)
        setStatus(write ? 'ready' : 'readonly')
      })
      .catch(e => {
        if (mountedRef.current && seq === requestSeq.current) setStatus(String(e))
      })
  }, [fs, path, target, ref, write])

  const save = React.useCallback(async () => {
    if (!write || status === 'loading' || status === 'saving') return
    const seq = ++requestSeq.current
    const savedEditVersion = editVersion.current
    setStatus('saving')
    try {
      await write(ref, content)
      if (!mountedRef.current || seq !== requestSeq.current || editVersion.current !== savedEditVersion) return
      setDirty(false)
      setStatus('saved')
    } catch (e) {
      if (mountedRef.current && seq === requestSeq.current) setStatus(String(e))
    }
  }, [write, ref, content, status])
  saveRef.current = () => void save()

  const saveKey = React.useMemo(() => keymap.of([{ key: 'Mod-s', preventDefault: true, run: () => { saveRef.current(); return true } }]), [])

  const requestClose = React.useCallback(async () => {
    if (status === 'saving') return
    if (dirty) {
      const discard = await confirmDialog({
        title: 'Discard unsaved edits?',
        message: 'Your unsaved project edits will be lost.',
        confirmLabel: 'Discard',
        danger: true,
      })
      if (!discard) return
    }
    onClose()
  }, [dirty, onClose, status])

  const displayPath = pathRef.current || path
  const name = displayPath.split('/').pop()
  const retained = dirty && !write
  const statusText = status === 'saved'
    ? 'Saved'
    : status === 'saving'
      ? 'Saving…'
      : status === 'ready'
        ? (dirty ? 'Unsaved · ⌘/Ctrl+S' : 'Up to date')
        : status === 'readonly'
          ? (dirty ? 'Unsaved · read-only during inspection' : 'Read-only inspection')
          : status === 'loading'
            ? 'Loading…'
            : status
  return <div className={`file-editor${retained ? ' file-editor-retained' : ''}`}>
    <div className="file-editor-head">
      <strong title={displayPath}>{name}{dirty ? ' •' : ''}</strong>
      <div>{write && <button className="ghost-button" onClick={() => void save()} disabled={status === 'loading' || status === 'saving'}>{status === 'saving' ? 'Saving…' : 'Save'}</button>}<button className="ghost-button" onClick={() => void requestClose()} disabled={status === 'saving'} title={closeTitle} aria-label={closeTitle}>{closeLabel}</button></div>
    </div>
    {retained && <div className="file-editor-retain-banner">Unsaved project edits · inspection tree stays available</div>}
    {status === 'loading'
      ? <p className="muted file-editor-loading">Loading…</p>
      : <div className="cm-wrap"><CodeMirror value={content} height="100%" theme={isDark ? oneDark : 'light'} editable={!!write} autoFocus={autoFocus} extensions={[...(write ? [saveKey] : []), ...langFor(displayPath)]} onChange={write ? v => { editVersion.current += 1; setContent(v); setDirty(true); setStatus('ready') } : undefined} basicSetup={{ lineNumbers: true, highlightActiveLine: true, foldGutter: true }} /></div>}
    <div className="file-editor-status muted" role="status" aria-live="polite">{statusText}</div>
  </div>
}
