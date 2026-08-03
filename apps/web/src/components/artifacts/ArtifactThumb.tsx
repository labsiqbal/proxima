import React from 'react'
import type { Artifact } from '../../api/files'
import { previewUrl, retargetFile } from '../../api/files'
import { collectArtboardMediaRefs, resolveProjectMediaSrc } from '../../api/projectMedia'
import { projectFs } from '../../api/fsAdapter'
import { useProjectMediaUrls } from '../../hooks/useProjectMediaUrls'
import { useRawBlobUrl } from '../../hooks/useRawBlobUrl'
import { MiniPreview } from '../design/MiniPreview'
import type { Artboard } from '../design/scene'
import type { FileTarget } from '../../types'
import { designAssetSrc, designScenePath, typeMeta } from './archive'

// One artifact, rendered small (ADR-0043). The Artifacts gallery shows what you
// LOOK at as a picture - designs, images, video - and leaves what you READ to a
// list, so a thumbnail component only has to answer "which of those is this,
// and what are its bytes":
// - design  → the first artboard, drawn from scene.json (same MiniPreview the
//             Design gallery and the record panel use)
// - image   → the preview endpoint directly, except SVG/XML, which the backend
//             serves inert (ADR-0042) and therefore needs authenticated raw bytes
// - video   → metadata-only poster frame; a grid must never download whole films
// - other   → the type glyph, so a document row still reads as its kind
// Opening an artifact is the caller's business; this component never navigates.
// `DesignPreview` is exported because a design has no bytes to render anywhere
// else: the inline viewer draws it the same way at stage size (#146).

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i
const VIDEO_EXT = /\.(mp4|webm|mov)$/i
const SVG_EXT = /\.svg$/i

export type ArtifactKind = 'design' | 'image' | 'video' | 'document'

/** Which shelf an artifact belongs on: a thumbnail, or a row in the list. */
export function artifactKind(artifact: Pick<Artifact, 'type' | 'path'>): ArtifactKind {
  if (artifact.type === 'design') return 'design'
  if (artifact.type === 'video' || artifact.type === 'video-file' || VIDEO_EXT.test(artifact.path)) return 'video'
  if (artifact.type === 'image' || IMAGE_EXT.test(artifact.path)) return 'image'
  return 'document'
}

export const isVisualArtifact = (artifact: Pick<Artifact, 'type' | 'path'>) =>
  artifactKind(artifact) !== 'document'

function ArtifactGlyph({ type }: { type: string }) {
  return <span className="artifacts-glyph" data-testid="artifact-glyph" aria-hidden="true">{typeMeta(type).ic}</span>
}

/** A design scene drawn at whatever size its container gives it. */
export function DesignPreview({ token, slug, artifact }: { token: string; slug: string; artifact: Artifact }) {
  const [art, setArt] = React.useState<Artboard | null | undefined>(undefined)
  const target = artifact.target
  React.useEffect(() => {
    let alive = true
    setArt(undefined)
    const ref: string | FileTarget = target
      ? retargetFile(target, designScenePath(target.path))
      : designScenePath(artifact.path)
    projectFs(token, slug).read(ref).then(file => {
      if (!alive) return
      try {
        const scene = JSON.parse(file.content) as { artboards?: Artboard[] }
        setArt(scene.artboards?.[0] || null)
      } catch {
        setArt(null)
      }
    }).catch(() => { if (alive) setArt(null) })
    return () => { alive = false }
  }, [token, slug, artifact.path, target])

  const mediaRefs = React.useMemo(() => collectArtboardMediaRefs(art || undefined), [art])
  const mediaUrls = useProjectMediaUrls(token, slug, mediaRefs)
  const resolveSrc = React.useCallback(
    (src: string, srcTarget?: FileTarget) =>
      resolveProjectMediaSrc(srcTarget ? src : designAssetSrc(artifact.path, src), srcTarget, slug, mediaUrls),
    [slug, mediaUrls, artifact.path],
  )
  if (art === undefined) return <span className="artifacts-thumb-pending" aria-hidden="true" />
  if (!art) return <ArtifactGlyph type="design" />
  return <span className="artifacts-thumb-design"><MiniPreview art={art} resolveSrc={resolveSrc} /></span>
}

function SvgThumb({ token, slug, artifact }: { token: string; slug: string; artifact: Artifact }) {
  const blob = useRawBlobUrl(token, slug, artifact.path, artifact.target)
  if (!blob.url) return <span className="artifacts-thumb-pending" aria-hidden="true" />
  return <img className="artifacts-thumb-img" src={blob.url} alt="" loading="lazy" />
}

export function ArtifactThumb({ token, slug, artifact }: {
  token: string
  slug: string
  artifact: Artifact
}) {
  const kind = artifactKind(artifact)
  if (kind === 'design') return <DesignPreview token={token} slug={slug} artifact={artifact} />
  if (kind === 'image') {
    if (SVG_EXT.test(artifact.path)) return <SvgThumb token={token} slug={slug} artifact={artifact} />
    return <img className="artifacts-thumb-img" src={previewUrl(slug, artifact.path, artifact.target)} alt="" loading="lazy" />
  }
  if (kind === 'video') {
    return <span className="artifacts-thumb-video">
      <video src={`${previewUrl(slug, artifact.path, artifact.target)}#t=0.1`} muted playsInline preload="metadata" />
      <i aria-hidden="true">▶</i>
    </span>
  }
  return <ArtifactGlyph type={artifact.type} />
}
