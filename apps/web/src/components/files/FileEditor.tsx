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
  onDirtyChange,
}: {
  fs: ReadOnlyFsAdapter
  write?: (ref: FileRef, content: string) => Promise<unknown>
  path: string
  target?: FileTarget
  onClose: () => void
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
    pathRef.current = path

    if (!pathChanged && dirtyRef.current) {
      setStatus(write ? 'ready' : 'readonly')
      return
    }

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

  const name = path.split('/').pop()
  return <div className="file-editor">
    <div className="file-editor-head">
      <strong title={path}>{name}{dirty ? ' •' : ''}</strong>
      <div>{write && <button className="ghost-button" onClick={() => void save()} disabled={status === 'loading' || status === 'saving'}>{status === 'saving' ? 'Saving…' : 'Save'}</button>}<button className="ghost-button" onClick={onClose} disabled={status === 'saving'}>Close</button></div>
    </div>
    {status === 'loading'
      ? <p className="muted" style={{ padding: '10px' }}>Loading…</p>
      : <div className="cm-wrap"><CodeMirror value={content} height="100%" theme={isDark ? oneDark : 'light'} editable={!!write} extensions={[...(write ? [saveKey] : []), ...langFor(path)]} onChange={write ? v => { editVersion.current += 1; setContent(v); setDirty(true); setStatus('ready') } : undefined} basicSetup={{ lineNumbers: true, highlightActiveLine: true, foldGutter: true }} /></div>}
    <div className="file-editor-status muted">{status === 'saved' ? 'Saved' : status === 'saving' ? 'Saving…' : status === 'ready' ? (dirty ? 'Unsaved · ⌘/Ctrl+S' : 'Up to date') : status === 'readonly' ? 'Read-only inspection' : status === 'loading' ? 'Loading…' : status}</div>
  </div>
}
