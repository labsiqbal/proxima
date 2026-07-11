import React from 'react'
import type { Project } from '../types'
import { appStatus, fileUrl, listArtifacts, type Artifact } from '../api/files'
import { listVideos, createVideo, deleteVideo, renderVideo, startVideoStudio, videoStudioProjectId, type VideoProject } from '../api/video'
import { confirmDialog } from '../components/ui/Dialog'
import { BackButton } from '../components/ui/BackButton'
import { IconTrash } from '../components/shell/icons'

const cleanProjectName = (name: string) => name.replace(/\s*\(private\)\s*$/i, '')
const videoStudioError = (value: unknown) => String(value).replaceAll('HyperFrames Studio', 'Video Studio').replaceAll('HyperFrames', 'Video')
const VideoStudioHost = React.lazy(() => import('./VideoStudioHost'))
type VideoSurface = 'social' | 'promo' | 'explainer' | 'motion'
type VideoTemplate = { name: string; hint: string; size: string; ratio: string; prompt: string }
const VIDEO_SURFACES: { key: VideoSurface; label: string }[] = [
  { key: 'social', label: 'Social' },
  { key: 'promo', label: 'Promo' },
  { key: 'explainer', label: 'Explainer' },
  { key: 'motion', label: 'Motion' },
]
const VIDEO_TEMPLATES: Record<VideoSurface, VideoTemplate[]> = {
  social: [
    { name: 'Reel / Short', hint: 'Fast vertical cut with captions', size: '1080×1920', ratio: '9 / 16', prompt: 'Create a 9:16 social reel with fast motion, bold kinetic typography, beat-driven captions, and a clear closing CTA.' },
    { name: 'Story Teaser', hint: 'Vertical teaser with a clean hook', size: '1080×1920', ratio: '9 / 16', prompt: 'Create a 9:16 story teaser with a strong opening hook, clean visual rhythm, and a simple final action.' },
    { name: 'Square Post', hint: 'Loopable square motion post', size: '1080×1080', ratio: '1 / 1', prompt: 'Create a loopable square motion post with layered type, subtle background movement, and a compact CTA.' },
  ],
  promo: [
    { name: 'Product Promo', hint: 'Offer-led launch sequence', size: '1080×1920', ratio: '9 / 16', prompt: 'Create a product promo video with a bold offer, product benefit beats, animated callouts, and a confident CTA.' },
    { name: 'Launch Trailer', hint: 'Punchy reveal and proof points', size: '1920×1080', ratio: '16 / 9', prompt: 'Create a launch trailer with a cinematic reveal, three proof points, smooth transitions, and a strong final frame.' },
    { name: 'Ad Variant', hint: 'Short paid-social hook', size: '1080×1350', ratio: '4 / 5', prompt: 'Create a paid social ad variant with a sharp hook, visual proof, objection handling, and direct CTA.' },
  ],
  explainer: [
    { name: 'Explainer', hint: 'Simple story beats', size: '1920×1080', ratio: '16 / 9', prompt: 'Create a simple explainer video with problem, solution, how it works, and next step scenes.' },
    { name: 'Feature Walkthrough', hint: 'UI-led feature tour', size: '1920×1080', ratio: '16 / 9', prompt: 'Create a feature walkthrough with clear section titles, UI callouts, and calm pacing.' },
    { name: 'How-to', hint: 'Step-by-step sequence', size: '1080×1920', ratio: '9 / 16', prompt: 'Create a vertical how-to video with numbered steps, concise captions, and a useful final recap.' },
  ],
  motion: [
    { name: 'Motion Graphic', hint: 'Typography and shape rhythm', size: '1080×1080', ratio: '1 / 1', prompt: 'Create a motion graphic with kinetic typography, abstract shapes, rhythmic transitions, and polished depth.' },
    { name: 'Title Card', hint: 'Animated opener', size: '1920×1080', ratio: '16 / 9', prompt: 'Create an animated title card with premium typography, layered depth, and a smooth reveal.' },
    { name: 'Loop Background', hint: 'Ambient reusable motion', size: '1920×1080', ratio: '16 / 9', prompt: 'Create a seamless loop background with soft motion, visual depth, and enough negative space for text overlays.' },
  ],
}

function useMobileVideoViewport() {
  const query = '(max-width: 760px), (pointer: coarse) and (max-width: 820px)'
  const [mobile, setMobile] = React.useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)
  React.useEffect(() => {
    const mq = window.matchMedia(query)
    const update = () => setMobile(mq.matches)
    update()
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])
  return mobile
}

function VideoThumbnail({ token, slug, video }: { token: string; slug: string; video: VideoProject }) {
  const boxRef = React.useRef<HTMLDivElement>(null)
  const width = Math.max(1, video.width || 1080)
  const height = Math.max(1, video.height || 1920)
  const [scale, setScale] = React.useState(0.16)
  React.useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const update = () => setScale(Math.max(0.01, el.clientWidth / width))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [width])
  return <div className="video-thumb" ref={boxRef} style={{ width: `min(100%, ${Math.round(width / height * 178)}px)`, aspectRatio: `${width} / ${height}` }}>
    <iframe
      title={`${video.title} thumbnail`}
      src={fileUrl(token, slug, `${video.path}/index.html`)}
      sandbox="allow-scripts allow-same-origin"
      scrolling="no"
      style={{ width, height, transform: `scale(${scale})` }}
    />
  </div>
}

function MobileVideoNotice({ token, slug, video, latestRender, onOpenArtifact }: { token: string; slug: string; video: VideoProject; latestRender: Artifact | null; onOpenArtifact?: (path: string) => void }) {
  const src = latestRender ? fileUrl(token, slug, latestRender.path) : ''
  return <div className="video-desktop-notice">
    <div>
      <strong>Video Studio is available on desktop.</strong>
      <p className="muted">Open this project on a desktop screen to edit the timeline, layers, motion, and render settings.</p>
      {latestRender ? <section className="video-render-card" aria-label="Latest rendered video">
        <div className="video-render-head">
          <span>Latest render</span>
          <small>{latestRender.path.split('/').pop() || 'Rendered video'}</small>
        </div>
        <video className="video-render-player" src={src} controls playsInline preload="metadata" />
        <div className="video-render-actions">
          {onOpenArtifact && <button className="ghost-button" onClick={() => onOpenArtifact(latestRender.path)}>Open in Artifacts</button>}
          <a className="ghost-button" href={src} download>Download</a>
        </div>
      </section> : <div className="video-render-empty">
        <span>No rendered video yet.</span>
        <p className="muted">Open this project on desktop to edit and render it, then review the exported video here.</p>
      </div>}
      <span>{video.title}</span>
    </div>
  </div>
}

export function VideoScreen({ token, project, openVideoId, onOpened, onExit, onOpenArtifact }: { token: string; project: Project | null; profileId?: number | null; openVideoId?: string | null; onOpened?: () => void; onExit?: () => void; onOpenArtifact?: (path: string) => void }) {
  const isMobile = useMobileVideoViewport()
  const [videos, setVideos] = React.useState<VideoProject[]>([])
  const [renderArtifacts, setRenderArtifacts] = React.useState<Artifact[]>([])
  const [selected, setSelected] = React.useState<VideoProject | null>(null)
  const [stage, setStage] = React.useState<'start' | 'gallery' | 'studio'>('start')
  const [brief, setBrief] = React.useState('')
  const [busy, setBusy] = React.useState<'load' | 'create' | 'studio' | 'render' | null>(null)
  const [error, setError] = React.useState('')
  const [studioReady, setStudioReady] = React.useState(false)
  const [renderPath, setRenderPath] = React.useState('')
  const [deletingId, setDeletingId] = React.useState<string | null>(null)
  const [studioWaitSeconds, setStudioWaitSeconds] = React.useState(0)
  const [surface, setSurface] = React.useState<VideoSurface>('social')
  const seqRef = React.useRef(0)
  const renderSeqRef = React.useRef(0)
  const videoTemplates = VIDEO_TEMPLATES[surface]
  const latestRender = React.useMemo(() => {
    if (!selected) return null
    const prefix = `${selected.path.replace(/\/$/, '')}/renders/`
    if (renderPath && renderPath.startsWith(prefix)) {
      return { type: 'video-file', title: renderPath.split('/').pop() || 'Rendered video', path: renderPath } as Artifact
    }
    return renderArtifacts.find(a => a.path.startsWith(prefix) && /\.(mp4|webm|mov)$/i.test(a.path)) || null
  }, [selected?.path, renderPath, renderArtifacts])

  const setStudioHash = React.useCallback((video: VideoProject) => {
    if (!project) return
    const studioId = videoStudioProjectId(project.slug, video.id)
    const nextHash = `#project/${encodeURIComponent(studioId)}`
    if (window.location.hash !== nextHash) window.history.replaceState(null, '', nextHash)
  }, [project?.slug])

  const clearStudioHash = React.useCallback(() => {
    if (window.location.hash.startsWith('#project/proxima-video__')) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
    }
  }, [])

  const load = React.useCallback(async () => {
    if (!project) { setVideos([]); setSelected(null); return }
    const seq = ++seqRef.current
    setBusy('load'); setError('')
    try {
      const r = await listVideos(token, project.slug)
      if (seq !== seqRef.current) return
      setVideos(r.videos)
    } catch (e) {
      if (seq === seqRef.current) setError(videoStudioError(e))
    } finally {
      if (seq === seqRef.current) setBusy(null)
    }
  }, [token, project?.slug])

  React.useEffect(() => { void load() }, [load])

  const refreshRenders = React.useCallback(async () => {
    if (!project) { setRenderArtifacts([]); return }
    const seq = ++renderSeqRef.current
    try {
      const r = await listArtifacts(token, project.slug, 525600)
      if (seq !== renderSeqRef.current) return
      setRenderArtifacts(r.artifacts.filter(a => (a.type === 'video-file' || /\.(mp4|webm|mov)$/i.test(a.path)) && /\/renders\/.+\.(mp4|webm|mov)$/i.test(a.path)))
    } catch {
      if (seq === renderSeqRef.current) setRenderArtifacts([])
    }
  }, [token, project?.slug])

  React.useEffect(() => {
    if (selected && stage === 'studio') void refreshRenders()
  }, [selected?.id, stage, refreshRenders])

  const create = async (nextBrief = brief, titleHint?: string) => {
    const text = nextBrief.trim()
    if (!project || !text || busy) return
    setBusy('create'); setError('')
    try {
      const title = titleHint || text.split(/\s+/).slice(0, 7).join(' ') || 'Untitled video'
      const v = await createVideo(token, project.slug, { title, brief: text })
      setVideos(cur => [v, ...cur.filter(x => x.id !== v.id)])
      setStudioHash(v)
      setSelected(v)
      setStudioReady(false)
      setBrief('')
      setStage('studio')
    } catch (e) {
      setError(videoStudioError(e))
    } finally {
      setBusy(null)
    }
  }

  React.useEffect(() => {
    if (!project || !selected || stage !== 'studio') return
    setStudioHash(selected)
  }, [project?.slug, selected, setStudioHash, stage])

  const openStudio = async (video = selected) => {
    if (!project || !video || busy === 'studio') return
    setBusy('studio'); setError(''); setStudioReady(false)
    try {
      await startVideoStudio(token, project.slug, video.id)
      for (let i = 0; i < 30; i++) {
        const status = await appStatus(token, project.slug)
        if (status.ready) break
        if (status.exited) throw new Error((status.log || []).slice(-8).join('\n') || 'Video Studio exited')
        await new Promise(r => setTimeout(r, 1000))
      }
      setStudioHash(video)
      setStudioReady(true)
    } catch (e) {
      setError(videoStudioError(e))
    } finally {
      setBusy(null)
    }
  }

  React.useEffect(() => {
    if (selected && stage === 'studio' && !isMobile) void openStudio(selected)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, stage, isMobile])

  React.useEffect(() => {
    if (busy !== 'studio' || studioReady) { setStudioWaitSeconds(0); return }
    const started = Date.now()
    const timer = window.setInterval(() => setStudioWaitSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [busy, studioReady, selected?.id])

  const render = async () => {
    if (!project || !selected || busy) return
    setBusy('render'); setError(''); setRenderPath('')
    try {
      const r = await renderVideo(token, project.slug, selected.id, { quality: 'draft', format: 'mp4' })
      setRenderPath(r.path)
      void refreshRenders()
    } catch (e) {
      setError(videoStudioError(e))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (video: VideoProject) => {
    if (!project || deletingId) return
    const ok = await confirmDialog({
      title: 'Delete video?',
      message: `“${video.title}” and its video artifact folder will be removed.`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    setDeletingId(video.id); setError('')
    try {
      await deleteVideo(token, project.slug, video.id)
      setVideos(cur => cur.filter(v => v.id !== video.id))
      if (selected?.id === video.id) {
        clearStudioHash()
        setSelected(null)
        setStudioReady(false)
        setRenderPath('')
        setStage('start')
      }
    } catch (e) {
      setError(videoStudioError(e))
    } finally {
      setDeletingId(null)
    }
  }

  const openVideo = (video: VideoProject) => {
    setStudioHash(video)
    setStudioReady(false)
    setSelected(video)
    setRenderPath('')
    setStage('studio')
  }

  React.useEffect(() => {
    if (!openVideoId || !videos.length) return
    const target = videos.find(v => v.id === openVideoId || v.path.split('/').filter(Boolean).slice(-1)[0] === openVideoId)
    if (!target) return
    openVideo(target)
    onOpened?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openVideoId, videos])

  if (!project) return <section className="video-shell"><div className="video-empty"><p className="muted">Pick a project first.</p></div></section>

  if (stage === 'start') return <section className="video-home">
    <div className="video-home-inner">
      <p className="muted ds-project-tag">Creating video in <strong>{cleanProjectName(project.name)}</strong> · saved to this project</p>
      <h1>What do you want to make?</h1>
      <p className="muted ds-sub">Describe the video and Proxima will draft a motion project you can open, preview, render, and keep iterating from this project.</p>
      <div className="video-prompt-box">
        <textarea rows={3} placeholder="Describe your video — e.g. product launch reel, 9:16, bold kinetic typography, upbeat motion" value={brief} onChange={e => setBrief(e.target.value)} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') void create() }} />
        <div className="ds-prompt-bar">
          <div className="ds-surface-pills">{VIDEO_SURFACES.map(s => <button key={s.key} className={surface === s.key ? 'active' : ''} onClick={() => setSurface(s.key)}>{s.label}</button>)}</div>
          <button className="primary-button" disabled={busy === 'create' || !brief.trim()} onClick={() => void create()}>{busy === 'create' ? 'Creating...' : 'Generate →'}</button>
        </div>
      </div>
      <p className="ds-or"><span>Or start from a template</span></p>
      <div className="video-template-row">
        {videoTemplates.map(t => <button key={t.name} className="ds-tpl video-template-card" disabled={busy === 'create'} onClick={() => void create(brief.trim() ? `${t.prompt} Extra direction: ${brief.trim()}` : t.prompt, t.name)}>
          <div className="ds-tpl-canvas video-tpl-canvas"><span className="video-mini-frame" style={{ aspectRatio: t.ratio }}>
            <i className="vm-title" /><i className="vm-line" /><i className="vm-line short" /><i className="vm-cta" /><i className="vm-play" />
          </span></div>
          <div className="ds-tpl-meta"><strong>{t.name}</strong><span className="ds-tpl-hint">{t.hint} · {t.size}</span></div>
        </button>)}
      </div>
      {videos.length > 0 && <button className="video-gallery-link" onClick={() => setStage('gallery')}>Your videos ({videos.length}) →</button>}
    </div>
    {error && <p className="error-text">{error}</p>}
  </section>

  if (stage === 'gallery') return <section className="video-gallery-page">
    <div className="video-gallery-head">
      <BackButton label="Back" onClick={() => setStage('start')} />
      <h2>Your videos</h2><span className="muted">{videos.length}</span>
      <span className="muted ds-project-tag">in <strong>{cleanProjectName(project.name)}</strong></span>
    </div>
    {videos.length === 0 ? <p className="muted ds-tip">No video projects yet.</p> : <div className="video-gallery-grid">{videos.map(v => {
      const w = v.width || 1080
      const h = v.height || 1920
      return <div className="video-tile" key={v.id} role="button" tabIndex={0} onClick={() => openVideo(v)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') openVideo(v) }}>
        <button className="video-card-delete" disabled={deletingId === v.id} onClick={e => { e.stopPropagation(); void remove(v) }} aria-label={`Delete ${v.title}`} title="Delete video"><IconTrash size={15} /></button>
        <div className="video-tile-canvas"><VideoThumbnail token={token} slug={project.slug} video={v} /></div>
        <div className="video-tile-meta"><strong>{v.title}</strong><span>{w}×{h} · {v.path}</span></div>
      </div>
    })}</div>}
    {error && <p className="error-text">{error}</p>}
  </section>

  return <section className="video-shell editor">
    <main className="video-studio-wrap">
      <div className="video-studio-bar">
        <button className="ghost-button" onClick={() => { clearStudioHash(); setStage('start'); setSelected(null); setStudioReady(false); onExit?.() }}>Back</button>
        <div><strong>{selected?.title || 'No video selected'}</strong><span className="muted">{selected?.path || 'Create or select a video project.'}</span></div>
        {!isMobile && <div className="video-actions">
          <button className="ghost-button" disabled={!selected || busy === 'studio'} onClick={() => void openStudio()}>{busy === 'studio' ? 'Opening Studio...' : 'Reload Studio'}</button>
          {renderPath && <button className="ghost-button" onClick={() => onOpenArtifact?.(renderPath)}>Open MP4</button>}
          <button className="ghost-button" disabled={!selected || busy === 'render'} onClick={() => void render()}>{busy === 'render' ? 'Rendering...' : 'Render MP4'}</button>
        </div>}
      </div>
      <div className="video-studio-frame video-studio-direct">
        {selected && isMobile ? <MobileVideoNotice token={token} slug={project.slug} video={selected} latestRender={latestRender} onOpenArtifact={onOpenArtifact} /> : selected && studioReady ? <React.Suspense fallback={<div className="video-empty loading"><div className="video-loading-state"><span className="video-loading-spinner" /><strong>Loading Video Studio...</strong><p className="muted">Preparing the editor bundle.</p></div></div>}><VideoStudioHost key={videoStudioProjectId(project.slug, selected.id)} /></React.Suspense> : <div className={`video-empty ${busy === 'studio' ? 'loading' : ''}`}>
          {busy === 'studio'
            ? <div className="video-loading-state"><span className="video-loading-spinner" /><strong>{studioWaitSeconds >= 10 ? 'Still starting Video Studio...' : 'Starting Video Studio...'}</strong><p className="muted">{studioWaitSeconds >= 10 ? 'Preparing the editor is taking longer than usual.' : 'Preparing the editor, timeline, and preview.'}</p></div>
            : <p className="muted">Select a video project to open Video Studio.</p>}
        </div>}
      </div>
      {error && <p className="error-text">{error}</p>}
    </main>
  </section>
}
