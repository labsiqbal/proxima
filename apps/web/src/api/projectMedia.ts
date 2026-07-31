import type { FileTarget } from '../types'
import { fetchRawFile, fileUrl, isSvgPath } from './files'

export type ProjectMediaRef = {
  src: string
  target?: FileTarget
}

export function isAbsoluteMediaSrc(src: string): boolean {
  return /^(https?:|data:|blob:)/i.test(src)
}

export function projectMediaKey(src: string, target?: FileTarget): string {
  if (!target) return JSON.stringify(['path', src])
  return JSON.stringify([
    'target',
    src,
    target.project,
    target.area.kind,
    target.area.id ?? null,
    target.path,
  ])
}

/** Inline display that must use authenticated raw bytes (never a preview entry). */
export function needsRawDisplayBlob(src: string, target?: FileTarget): boolean {
  if (!src || isAbsoluteMediaSrc(src) || /^gen:/i.test(src)) return false
  return Boolean(target) || isSvgPath(src)
}

export function resolveProjectMediaSrc(
  src: string,
  target: FileTarget | undefined,
  slug: string | null | undefined,
  urls: Record<string, string>,
): string {
  if (!src) return ''
  if (/^gen:/i.test(src)) return ''
  if (isAbsoluteMediaSrc(src)) return src
  if (needsRawDisplayBlob(src, target)) {
    return urls[projectMediaKey(src, target)] || ''
  }
  return slug ? fileUrl(slug, src, target) : src
}

function revokeObjectUrl(url: string) {
  if (typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url)
}

/** Authenticated/raw when required; otherwise the resolved preview/absolute URL. */
export async function loadProjectMediaBlob(
  token: string,
  slug: string,
  src: string,
  target?: FileTarget,
  resolvedUrl?: string,
): Promise<Blob> {
  if (!src || /^gen:/i.test(src)) throw new Error('missing media source')
  if (isAbsoluteMediaSrc(src) && !needsRawDisplayBlob(src, target)) {
    const response = await fetch(src)
    if (!response.ok) throw new Error(`Could not download ${src}`)
    return response.blob()
  }
  if (needsRawDisplayBlob(src, target)) {
    return fetchRawFile(token, slug, src, target)
  }
  const url = resolvedUrl || (slug ? fileUrl(slug, src, target) : '')
  if (!url) throw new Error(`Could not resolve ${src}`)
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Could not download ${src}`)
  return response.blob()
}

export function measureImageBlob(blob: Blob): Promise<{ w: number; h: number }> {
  const objectUrl = URL.createObjectURL(blob)
  return new Promise((resolve, reject) => {
    const img = new window.Image()
    img.onload = () => {
      const size = {
        w: img.naturalWidth || img.width || 1,
        h: img.naturalHeight || img.height || 1,
      }
      revokeObjectUrl(objectUrl)
      resolve(size)
    }
    img.onerror = () => {
      revokeObjectUrl(objectUrl)
      reject(new Error('Could not decode image'))
    }
    img.src = objectUrl
  })
}

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('Could not read image bytes'))
    reader.readAsDataURL(blob)
  })
}

/** Size a project image without depending on a pre-hydrated resolveSrc map. */
export async function measureProjectMedia(
  token: string,
  slug: string,
  src: string,
  target?: FileTarget,
  resolvedUrl?: string,
): Promise<{ w: number; h: number }> {
  if (!src) throw new Error('missing media source')
  if (needsRawDisplayBlob(src, target)) {
    const blob = await loadProjectMediaBlob(token, slug, src, target)
    return measureImageBlob(blob)
  }
  const url = resolvedUrl || (isAbsoluteMediaSrc(src) ? src : fileUrl(slug, src, target))
  if (!url) throw new Error(`Could not resolve ${src}`)
  return new Promise((resolve, reject) => {
    const img = new window.Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve({
      w: img.naturalWidth || img.width || 1,
      h: img.naturalHeight || img.height || 1,
    })
    img.onerror = () => reject(new Error(`Could not decode ${src}`))
    img.src = url
  })
}

/** Export bytes as a data URL; raw-fetch SVG/target media, never hang on empty src. */
export async function projectMediaDataUrl(
  token: string,
  slug: string,
  src: string,
  target?: FileTarget,
  resolvedUrl?: string,
): Promise<string> {
  if (!src || /^gen:/i.test(src)) return ''
  try {
    const blob = await loadProjectMediaBlob(token, slug, src, target, resolvedUrl)
    return await blobToDataUrl(blob)
  } catch {
    return ''
  }
}

type ArtboardLike = {
  layers?: Array<{
    type?: string
    src?: string
    target?: FileTarget
    imageSrc?: string
    imageTarget?: FileTarget
  }>
}

export function collectArtboardMediaRefs(art?: ArtboardLike | null): ProjectMediaRef[] {
  if (!art?.layers?.length) return []
  const refs = new Map<string, ProjectMediaRef>()
  for (const layer of art.layers) {
    if (layer.type === 'image' && layer.src) {
      const ref = { src: layer.src, target: layer.target }
      if (needsRawDisplayBlob(ref.src, ref.target)) {
        refs.set(projectMediaKey(ref.src, ref.target), ref)
      }
    }
    if (layer.imageSrc) {
      const ref = { src: layer.imageSrc, target: layer.imageTarget }
      if (needsRawDisplayBlob(ref.src, ref.target)) {
        refs.set(projectMediaKey(ref.src, ref.target), ref)
      }
    }
  }
  return [...refs.values()]
}

export function mergeProjectMediaRefs(
  ...groups: Array<Iterable<ProjectMediaRef> | ProjectMediaRef[] | undefined>
): ProjectMediaRef[] {
  const refs = new Map<string, ProjectMediaRef>()
  for (const group of groups) {
    if (!group) continue
    for (const ref of group) {
      if (!ref?.src || !needsRawDisplayBlob(ref.src, ref.target)) continue
      refs.set(projectMediaKey(ref.src, ref.target), {
        src: ref.src,
        target: ref.target,
      })
    }
  }
  return [...refs.values()]
}
