import React from 'react'
import {
  Excalidraw,
  THEME,
  convertToExcalidrawElements,
  hashElementsVersion,
  serializeAsJSON,
} from '@excalidraw/excalidraw'
import excalidrawStyles from '@excalidraw/excalidraw/index.css?inline'
import { parseMermaidToExcalidraw } from '@excalidraw/mermaid-to-excalidraw'
import type {
  BinaryFiles,
  ExcalidrawImperativeAPI,
  ExcalidrawInitialDataState,
} from '@excalidraw/excalidraw/types'
import { projectFs } from '../../api/fsAdapter'
import { sourceFingerprint, whiteboardPathFor } from './artifactReview'

const EXCALIDRAW_STYLE_ID = 'proxima-excalidraw-styles'

function ensureExcalidrawStyles(): void {
  if (document.getElementById(EXCALIDRAW_STYLE_ID)) return
  const style = document.createElement('style')
  style.id = EXCALIDRAW_STYLE_ID
  // The package stylesheet's bundled Assistant font URLs are relative to its own
  // package directory. Proxima uses its existing UI font instead of emitting broken
  // page-relative requests when the CSS is loaded lazily as text.
  style.textContent = excalidrawStyles.replace(/@font-face\s*{[^}]*}/g, '')
  document.head.appendChild(style)
}

ensureExcalidrawStyles()

type SavedWhiteboard = ExcalidrawInitialDataState & {
  proxima?: {
    sourcePath?: string
    sourceFingerprint?: string
    diagramIndex?: number
  }
}

async function convertMermaid(source: string): Promise<ExcalidrawInitialDataState> {
  const converted = await parseMermaidToExcalidraw(source, {
    flowchart: { curve: 'linear' },
    maxEdges: 500,
    maxTextSize: 50_000,
  })
  return {
    elements: convertToExcalidrawElements(converted.elements, { regenerateIds: false }),
    files: converted.files as BinaryFiles | undefined,
    appState: { scrollX: 0, scrollY: 0 },
  }
}

export function ExcalidrawWhiteboard({ token, slug, sourcePath, source, diagramIndex, onClose, onSaved }: {
  token: string
  slug: string
  sourcePath: string
  source: string
  diagramIndex: number
  onClose: () => void
  onSaved: (path: string) => void
}) {
  const fs = React.useMemo(() => projectFs(token, slug), [token, slug])
  const scenePath = React.useMemo(() => whiteboardPathFor(sourcePath, diagramIndex), [sourcePath, diagramIndex])
  const fingerprint = React.useMemo(() => sourceFingerprint(source), [source])
  const [initialData, setInitialData] = React.useState<ExcalidrawInitialDataState | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState('')
  const [dirty, setDirty] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [saved, setSaved] = React.useState(false)
  const [sourceChanged, setSourceChanged] = React.useState(false)
  const apiRef = React.useRef<ExcalidrawImperativeAPI | null>(null)
  const savedSceneVersion = React.useRef<number | null>(null)

  const rebuild = React.useCallback(async () => {
    setError('')
    try {
      const next = await convertMermaid(source)
      const api = apiRef.current
      if (api) {
        api.updateScene({ elements: next.elements })
        if (next.files) api.addFiles(Object.values(next.files))
        api.scrollToContent(next.elements || [], { fitToContent: true })
      } else {
        setInitialData(next)
      }
      setSourceChanged(false)
      setDirty(true)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not convert this Mermaid diagram.')
    }
  }, [source])

  React.useEffect(() => {
    let alive = true
    setLoading(true)
    setInitialData(null)
    setError('')
    setDirty(false)
    setSaved(false)
    setSourceChanged(false)
    savedSceneVersion.current = null
    void fs.read(scenePath).then(file => {
      if (!alive) return
      try {
        const parsed = JSON.parse(file.content) as SavedWhiteboard
        if (!Array.isArray(parsed.elements)) throw new Error('The saved whiteboard is not a valid Excalidraw scene.')
        setInitialData(parsed)
        savedSceneVersion.current = hashElementsVersion(parsed.elements || [])
        setSourceChanged(parsed.proxima?.sourceFingerprint !== fingerprint)
        setSaved(true)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Could not load the saved whiteboard.')
      }
    }).catch(async () => {
      try {
        const converted = await convertMermaid(source)
        if (alive) setInitialData(converted)
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : 'Could not convert this Mermaid diagram.')
      }
    }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [fs, scenePath, fingerprint, source])

  const save = async () => {
    const api = apiRef.current
    if (!api || saving) return
    setSaving(true)
    setError('')
    try {
      const elements = api.getSceneElementsIncludingDeleted()
      const serialized = serializeAsJSON(
        elements,
        api.getAppState(),
        api.getFiles(),
        'local',
      )
      const document = JSON.parse(serialized) as Record<string, unknown>
      document.proxima = { sourcePath, sourceFingerprint: fingerprint, diagramIndex }
      await fs.mkdir('artifacts/whiteboards').catch(() => undefined)
      await fs.write(scenePath, JSON.stringify(document, null, 2))
      savedSceneVersion.current = hashElementsVersion(elements)
      setDirty(false)
      setSaved(true)
      setSourceChanged(false)
      onSaved(scenePath)
      window.dispatchEvent(new CustomEvent('proxima:files-changed'))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save this whiteboard.')
    } finally {
      setSaving(false)
    }
  }

  const theme = document.documentElement.dataset.theme === 'dark' ? THEME.DARK : THEME.LIGHT

  return <section className="av-whiteboard" aria-label="Editable diagram whiteboard">
    <header className="av-whiteboard-bar">
      <div>
        <strong>Editable whiteboard</strong>
        <span className="mono" title={scenePath}>{scenePath}</span>
      </div>
      <div className="av-whiteboard-actions">
        <button type="button" className="ghost-button" onClick={onClose}>Back to artifact</button>
        <button type="button" className="primary-button" disabled={!initialData || saving || (!dirty && saved)} onClick={() => void save()}>
          {saving ? 'Saving...' : saved && !dirty ? 'Saved' : 'Save whiteboard'}
        </button>
      </div>
    </header>
    <div className="av-whiteboard-status">
      {sourceChanged && <div className="av-whiteboard-notice" role="status">
        <span><strong>The Mermaid source changed.</strong> Keep your saved edits, or rebuild the board from the current diagram.</span>
        <button type="button" className="ghost-button" onClick={() => { setSourceChanged(false); setDirty(true) }}>Keep edits</button>
        <button type="button" className="ghost-button" onClick={() => void rebuild()}>Rebuild from diagram</button>
      </div>}
      {error && <div className="error-bar">Whiteboard: {error}{!initialData && <button type="button" className="ghost-button" onClick={() => void rebuild()}>Rebuild from diagram</button>}</div>}
    </div>
    <div className="av-whiteboard-canvas">
      {loading && <div className="av-msg muted">Preparing editable diagram...</div>}
      {!loading && initialData && <Excalidraw
        initialData={initialData}
        excalidrawAPI={api => { apiRef.current = api }}
        theme={theme}
        name={sourcePath.split('/').pop() || 'Diagram'}
        autoFocus
        UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false } }}
        onChange={elements => setDirty(savedSceneVersion.current == null || hashElementsVersion(elements) !== savedSceneVersion.current)}
      />}
    </div>
  </section>
}
