import type { FileTarget } from '../types'
import { fileUrl, isSvgPath } from './files'

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
