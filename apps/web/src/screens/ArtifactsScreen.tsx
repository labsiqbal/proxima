import React from 'react'
import type { OutputLink, Project } from '../types'
import { projectFs } from '../api/fsAdapter'
import { detectApps, fetchRawBlob, listArtifacts, previewUrl, type Artifact } from '../api/files'
import { MessageContent } from '../components/chat/MessageContent'
import { Dropdown } from '../components/ui/Dropdown'
import { AppRunner } from '../components/files/AppRunner'
import { BackButton } from '../components/ui/BackButton'
import { MiniPreview } from '../components/design/MiniPreview'
import type { Artboard } from '../components/design/scene'

const FileEditor = React.lazy(() => import('../components/files/FileEditor').then(m => ({ default: m.FileEditor })))

const clean = (n: string) => n.replace(/\s*\(private\)\s*$/i, '')
const IMG = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i
const VIDEO = /\.(mp4|webm|mov)$/i
const RENDERED_MP4 = /\.mp4$/i
const HTML = /\.html?$/i
const MD = /\.(md|markdown)$/i
const resolveArtifactSrc = (token: string, slug: string, src: string) =>
  /^gen:/i.test(src) ? '' : (/^(https?:|data:|blob:)/.test(src) ? src : previewUrl(slug, src))

type Filter = 'all' | 'design' | 'video' | 'image' | 'document' | 'app' | 'data' | 'other'
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'design', label: 'Design' },
  { key: 'video', label: 'Video' },
  { key: 'image', label: 'Image' },
  { key: 'document', label: 'Document' },
  { key: 'app', label: 'App' },
  { key: 'data', label: 'Data' },
  { key: 'other', label: 'Other' },
]
const VISUAL_PAGE_SIZE = 18

const asArtifact = (a: Artifact | OutputLink): Artifact => ({
  type: a.type as Artifact['type'],
  title: a.title || a.path,
  path: a.path,
  id: a.id,
  dir: a.dir,
  command: a.command,
  project_slug: a.project_slug,
})

const category = (a: Artifact): Filter => {
  if (a.type === 'design') return 'design'
  if ((a.type === 'video-file' || RENDERED_MP4.test(a.path)) && RENDERED_MP4.test(a.path)) return 'video'
  if (a.type === 'app') return 'app'
  if (a.type === 'image' || IMG.test(a.path)) return 'image'
  if (a.type === 'doc' || a.type === 'page' || /\.(md|markdown|pdf|docx?|txt|rtf|html?)$/i.test(a.path)) return 'document'
  if (/\.(csv|json|xlsx?|db|sqlite)$/i.test(a.path)) return 'data'
  return 'other'
}
const isVisualArtifact = (a: Artifact) => ['design', 'image', 'video'].includes(category(a))

const icon = (a: Artifact) =>
  category(a) === 'design' ? '◆'
  : category(a) === 'video' ? '◉'
  : category(a) === 'app' ? '▶'
  : category(a) === 'image' ? '▧'
  : category(a) === 'document' ? '□'
  : category(a) === 'data' ? '▦'
  : '◇'

type DesignThumb = { art?: Artboard }

function ArtifactCard({ artifact, active, slug, token, designThumb, onOpen }: { artifact: Artifact; active: boolean; slug: string; token: string; designThumb?: DesignThumb; onOpen: () => void }) {
  const cat = category(artifact)
  const visual = cat === 'design' || cat === 'image' || cat === 'video'
  const title = artifact.title || artifact.path
  return <button className={`art-card ${visual ? 'visual' : ''} ${active ? 'active' : ''}`} onClick={onOpen}>
    {visual ? <span className="art-thumb">
      {cat === 'design' && designThumb?.art
        ? <MiniPreview art={designThumb.art} resolveSrc={s => resolveArtifactSrc(token, slug, s)} />
        : cat === 'image'
          ? <img src={previewUrl(slug, artifact.path)} alt={title} loading="lazy" />
          : cat === 'video'
            ? <span className="art-video-thumb"><video src={`${previewUrl(slug, artifact.path)}#t=0.1`} muted playsInline preload="metadata" /><i aria-hidden="true">▶</i></span>
          : <span className="art-ic">{icon(artifact)}</span>}
    </span> : <span className="art-ic">{icon(artifact)}</span>}
    <span className="art-meta"><strong>{title}</strong><small>{artifact.path}{artifact.command ? ` · ${artifact.command}` : ''}</small></span>
    {!visual && <span className="art-go">{artifact.type === 'app' ? 'Preview' : artifact.type === 'design' ? 'Open' : 'View'}</span>}
  </button>
}

function ArtifactList({ artifacts, selected, onOpen }: { artifacts: Artifact[]; selected: Artifact | null; onOpen: (a: Artifact) => void }) {
  return <div className="artifact-list">
    {artifacts.map(a => <button key={`${a.type}:${a.path}:${a.command || ''}`} className={`art-card ${selected?.path === a.path ? 'active' : ''}`} onClick={() => onOpen(a)}>
      <span className="art-ic">{icon(a)}</span>
      <span className="art-meta"><strong>{a.title || a.path}</strong><small>{a.path}{a.command ? ` · ${a.command}` : ''}</small></span>
      <span className="art-go">{a.type === 'app' ? 'Preview' : a.type === 'design' ? 'Open' : 'View'}</span>
    </button>)}
  </div>
}

function FileView({ token, slug, path, fs, onClose }: { token: string; slug: string; path: string; fs: ReturnType<typeof projectFs>; onClose: () => void }) {
  const name = path.split('/').pop() || path
  const previewable = HTML.test(path) || MD.test(path) || VIDEO.test(path)
  const [mode, setMode] = React.useState<'preview' | 'source'>(previewable ? 'preview' : 'source')
  const [img, setImg] = React.useState<string | null>(null)
  const [md, setMd] = React.useState<string | null>(null)
  const loadSeq = React.useRef(0)
  const blobUrlRef = React.useRef<string | null>(null)

  React.useEffect(() => { setMode(HTML.test(path) || MD.test(path) || VIDEO.test(path) ? 'preview' : 'source') }, [path])

  React.useEffect(() => {
    const seq = ++loadSeq.current
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current)
      blobUrlRef.current = null
    }
    setImg(null)
    setMd(null)
    if (IMG.test(path) || VIDEO.test(path)) {
      fetchRawBlob(token, slug, path).then(u => {
        if (seq !== loadSeq.current) {
          URL.revokeObjectURL(u)
          return
        }
        if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current)
        blobUrlRef.current = u
        setImg(u)
      }).catch(() => {})
    } else if (MD.test(path) && mode === 'preview') {
      fs.read(path)
        .then(b => {
          if (seq === loadSeq.current) setMd(b.content)
        })
        .catch(() => {
          if (seq === loadSeq.current) setMd('')
        })
    }
    return () => {
      if (seq === loadSeq.current) loadSeq.current += 1
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current)
        blobUrlRef.current = null
      }
    }
  }, [token, slug, path, mode, fs])

  if (IMG.test(path)) return <div className="file-editor"><div className="file-editor-head"><strong>{name}</strong><button className="ghost-button" onClick={onClose}>Close</button></div><div className="file-preview img">{img && <img src={img} alt={name} />}</div></div>
  if (VIDEO.test(path)) return <div className="file-editor"><div className="file-editor-head"><strong>{name}</strong><button className="ghost-button" onClick={onClose}>Close</button></div><div className="file-preview video">{img && <video className="art-video" src={img} controls playsInline />}</div></div>
  if (mode === 'source') return <React.Suspense fallback={<div className="file-editor"><div className="file-editor-head"><strong>{name}</strong></div><p className="muted" style={{ padding: '10px' }}>Loading editor...</p></div>}><FileEditor fs={fs} path={path} onClose={previewable ? () => setMode('preview') : onClose} /></React.Suspense>
  return <div className="file-editor">
    <div className="file-editor-head">
      <strong title={path}>{name}</strong>
      <div className="seg sm"><button className="active">Preview</button><button onClick={() => setMode('source')}>Source</button></div>
      <button className="ghost-button" onClick={onClose}>Close</button>
    </div>
    {HTML.test(path)
      ? <iframe className="file-preview-frame" title={name} src={previewUrl(slug, path)} sandbox="allow-scripts allow-same-origin" />
      : <div className="file-preview md-doc"><div className="md">{md != null ? <MessageContent content={md} /> : <p className="muted">Loading…</p>}</div></div>}
  </div>
}

export function ArtifactsScreen({ token, projects, activeProject, pendingFile, pendingArtifact, onPendingConsumed, onPendingArtifactConsumed, onActiveProject, onBackToChat, designStudioEnabled = false, onOpenDesign }: {
  token: string
  projects: Project[]
  activeProject: Project | null
  pendingFile?: { slug: string; path: string } | null
  pendingArtifact?: OutputLink | null
  onPendingConsumed?: () => void
  onPendingArtifactConsumed?: () => void
  onActiveProject?: (p: Project) => void
  onBackToChat?: () => void
  designStudioEnabled?: boolean
  onOpenDesign?: (id: string) => void
}) {
  const [slug, setSlug] = React.useState(activeProject?.slug || projects[0]?.slug || '')
  const [filter, setFilter] = React.useState<Filter>('all')
  const [visualPage, setVisualPage] = React.useState(0)
  const [artifacts, setArtifacts] = React.useState<Artifact[]>([])
  const [designThumbs, setDesignThumbs] = React.useState<Record<string, DesignThumb>>({})
  const [selected, setSelected] = React.useState<Artifact | null>(null)
  const [path, setPath] = React.useState<string | null>(null)
  const [runner, setRunner] = React.useState<Artifact | null>(null)
  const [listHidden, setListHidden] = React.useState(false)
  const [loading, setLoading] = React.useState(false)
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)
  const project = projects.find(p => p.slug === slug) || null
  const fs = React.useMemo(() => project ? projectFs(token, project.slug) : null, [token, project?.slug])

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
    }
  }, [])

  const pickProject = React.useCallback((nextSlug: string) => {
    if (!mountedRef.current) return
    setSlug(nextSlug)
    const next = projects.find(p => p.slug === nextSlug)
    if (next) onActiveProject?.(next)
  }, [projects, onActiveProject])

  React.useEffect(() => {
    if (activeProject?.slug && activeProject.slug !== slug) setSlug(activeProject.slug)
  }, [activeProject?.slug, slug])

  const load = React.useCallback(async () => {
    if (!slug) return
    const seq = ++loadSeq.current
    setLoading(true)
    try {
      const [arts, apps] = await Promise.all([
        listArtifacts(token, slug, 525600).catch(() => ({ artifacts: [] })),
        detectApps(token, slug).catch(() => ({ apps: [] })),
      ])
      if (!mountedRef.current || seq !== loadSeq.current) return
      const appArtifacts: Artifact[] = apps.apps.map(a => ({ type: 'app', title: a.dir && a.dir !== '.' ? a.dir : clean(project?.name || 'App'), path: a.dir || '.', dir: a.dir, command: a.command }))
      const merged = new Map<string, Artifact>()
      for (const a of [...arts.artifacts, ...appArtifacts]) merged.set(`${a.type}:${a.path}:${a.command || ''}`, a)
      const nextArtifacts = [...merged.values()]
      setArtifacts(nextArtifacts)
      const thumbs: Record<string, DesignThumb> = {}
      await Promise.all(nextArtifacts.filter(a => a.type === 'design').map(async a => {
        try {
          const scenePath = `${a.path.replace(/\/$/, '')}/scene.json`
          const f = await projectFs(token, slug).read(scenePath)
          const s = JSON.parse(f.content)
          const art = s.artboards?.[0]
          if (art) thumbs[a.path] = { art }
        } catch { /* thumbnail optional */ }
      }))
      if (mountedRef.current && seq === loadSeq.current) setDesignThumbs(thumbs)
    } finally {
      if (mountedRef.current && seq === loadSeq.current) setLoading(false)
    }
  }, [token, slug, project?.name])

  React.useEffect(() => { void load() }, [load])
  React.useEffect(() => {
    setSelected(null); setPath(null); setRunner(null)
  }, [slug])
  React.useEffect(() => { setVisualPage(0) }, [filter, slug])
  React.useEffect(() => {
    if (!mountedRef.current || !pendingFile) return
    if (pendingFile.slug !== slug) {
      pickProject(pendingFile.slug)
      return
    }
    setPath(pendingFile.path); setSelected(null); setRunner(null); onPendingConsumed?.()
  }, [pendingFile, slug, pickProject, onPendingConsumed])
  React.useEffect(() => {
    if (!mountedRef.current || !pendingArtifact) return
    if (pendingArtifact.project_slug && pendingArtifact.project_slug !== slug) {
      pickProject(pendingArtifact.project_slug)
      return
    }
    const a = asArtifact(pendingArtifact)
    if (a.type === 'app') setRunner(a)
    else { setSelected(a); setPath(a.type === 'design' ? `${a.path}/scene.json` : a.type === 'video' ? `${a.path}/index.html` : a.path); setRunner(null) }
    onPendingArtifactConsumed?.()
  }, [pendingArtifact, slug, pickProject, onPendingArtifactConsumed])

  if (projects.length === 0) return <section className="placeholder-view"><div className="assistant-bubble compact"><h1>Artifacts</h1><p>No projects yet.</p></div></section>

  const filtered = artifacts.filter(a => filter === 'all' || category(a) === filter)
  const visualItems = (filter === 'all' ? artifacts : filtered).filter(isVisualArtifact)
  const listItems = filter === 'all' ? artifacts.filter(a => !isVisualArtifact(a)) : filtered.filter(a => !isVisualArtifact(a))
  const showVisual = filter === 'all' || filter === 'design' || filter === 'image' || filter === 'video'
  const showList = filter === 'all' || !(filter === 'design' || filter === 'image' || filter === 'video')
  const visualPages = Math.max(1, Math.ceil(visualItems.length / VISUAL_PAGE_SIZE))
  const safeVisualPage = Math.min(visualPage, visualPages - 1)
  const pagedVisuals = visualItems.slice(safeVisualPage * VISUAL_PAGE_SIZE, safeVisualPage * VISUAL_PAGE_SIZE + VISUAL_PAGE_SIZE)
  const counts = FILTERS.reduce<Record<string, number>>((acc, f) => {
    acc[f.key] = f.key === 'all' ? artifacts.length : artifacts.filter(a => category(a) === f.key).length
    return acc
  }, {})
  const openArtifact = (a: Artifact) => {
    if (!mountedRef.current) return
    setSelected(a)
    setPath(null)
    setRunner(null)
    if (a.type === 'design' && designStudioEnabled) {
      onOpenDesign?.(a.id || a.path.split('/').filter(Boolean).slice(-1)[0])
    } else if (a.type === 'design') {
      setPath(`${a.path.replace(/\/$/, '')}/scene.json`)
    } else if (a.type === 'video') {
      setPath(`${a.path}/index.html`)
    } else if (a.type === 'app') {
      setRunner(a)
    } else {
      setPath(a.path)
    }
  }

  return <section className="artifacts-view">
    <div className="artifacts-head">
      {onBackToChat && <BackButton label="Back to chat" onClick={onBackToChat} />}
      <div>
        <h2>Artifacts</h2>
        <p className="muted">Outputs and app previews for the active project.</p>
      </div>
      <Dropdown value={slug} onChange={pickProject} minWidth={200} options={projects.map(p => ({ value: p.slug, label: clean(p.name) }))} />
      {(path || runner) && <button className="ghost-button" onClick={() => setListHidden(h => !h)} title={listHidden ? 'Show the artifact list' : 'Hide the list for a bigger preview'}>{listHidden ? 'Show list' : 'Hide list'}</button>}
      <button className="ghost-button" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button>
    </div>
    <div className="artifact-filters">
      {FILTERS.map(f => <button key={f.key} className={filter === f.key ? 'active' : ''} onClick={() => setFilter(f.key)}>{f.label}<span>{counts[f.key] || 0}</span></button>)}
    </div>
    <div className={`artifacts-body ${path || runner ? 'has-preview' : ''} ${(path || runner) && listHidden ? 'list-hidden' : ''}`}>
      <div className="artifacts-main">
        {showVisual && <section className={`artifact-section ${filter !== 'all' ? 'full' : ''}`}>
          <div className="artifact-section-head">
            <div><h3>{filter === 'video' ? 'Rendered videos' : filter === 'image' ? 'Images' : filter === 'design' ? 'Designs' : 'Visual gallery'}</h3><p className="muted">{visualItems.length} {filter === 'video' ? 'MP4 render' : 'visual artifact'}{visualItems.length === 1 ? '' : 's'}</p></div>
            {visualItems.length > VISUAL_PAGE_SIZE && <div className="artifact-pager">
              <button className="ghost-button sm" disabled={safeVisualPage === 0} onClick={() => setVisualPage(p => Math.max(0, p - 1))}>Previous</button>
              <span>{safeVisualPage + 1} / {visualPages}</span>
              <button className="ghost-button sm" disabled={safeVisualPage >= visualPages - 1} onClick={() => setVisualPage(p => Math.min(visualPages - 1, p + 1))}>Next</button>
            </div>}
          </div>
          {visualItems.length === 0 ? <div className="art-empty"><p className="muted">{loading ? 'Scanning project outputs…' : 'No visual artifacts found.'}</p></div>
            : <div className="artifact-masonry">{pagedVisuals.map(a => <ArtifactCard key={`${a.type}:${a.path}:${a.command || ''}`} artifact={a} active={selected?.path === a.path} slug={slug} token={token} designThumb={designThumbs[a.path]} onOpen={() => openArtifact(a)} />)}</div>}
        </section>}
        {showList && <section className="artifact-section">
          <div className="artifact-section-head"><div><h3>{filter === 'all' ? 'Files, apps, and documents' : FILTERS.find(f => f.key === filter)?.label || 'Artifacts'}</h3><p className="muted">{listItems.length} item{listItems.length === 1 ? '' : 's'}</p></div></div>
          {listItems.length === 0 ? <div className="art-empty"><p className="muted">{loading ? 'Scanning project outputs…' : 'No artifacts found for this filter.'}</p></div> : <ArtifactList artifacts={listItems} selected={selected} onOpen={openArtifact} />}
        </section>}
      </div>
      {(path || runner) && <div className="artifact-preview">
        {runner && project ? <AppRunner token={token} slug={project.slug} initialDir={runner.dir || (runner.path === '.' ? '' : runner.path)} initialCommand={runner.command} onClose={() => setRunner(null)} />
          : path && fs && project ? <FileView key={`${project.slug}:${path}`} token={token} slug={project.slug} path={path} fs={fs} onClose={() => setPath(null)} />
          : <div className="art-preview-empty"><p className="muted">Select an artifact to preview it here.</p></div>}
      </div>}
    </div>
  </section>
}
