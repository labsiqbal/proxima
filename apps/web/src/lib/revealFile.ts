import type { FileRootSide } from '../api/files'

// One contract for "show me where this is on disk". Any surface can raise it
// without prop-drilling through the shell - a deliverable record's "Reveal in
// Files", an Ops-migration recovery inspection - and exactly one surface answers
// it: the dock's Files tool (#145). Both ends live here so adding a field is one
// edit rather than three, and so the listener parses an untrusted CustomEvent
// detail in one audited place.

export const REVEAL_FILE_EVENT = 'proxima:reveal-file'

export type RevealTarget = {
  /** Which Container's files - an Ops migration can name one that is not active. */
  projectSlug: string
  path: string
  pathKind: 'root' | 'directory' | 'file'
  /** 'container' is the read-only recovery side; ordinary browsing is 'virtual'. */
  rootSide: FileRootSide
}

export type RevealRequest = {
  path: string
  projectSlug?: string
  pathKind?: RevealTarget['pathKind']
  rootSide?: FileRootSide
}

/** Ask the dock browser to show a path. A path is the only required part. */
export function revealFile(request: RevealRequest) {
  window.dispatchEvent(new CustomEvent(REVEAL_FILE_EVENT, { detail: request }))
}

/**
 * Read a reveal off the event. Returns null when it names no path or no
 * Container (the caller passes the active slug as the fallback), so a malformed
 * or out-of-context reveal can never point a tree at nothing.
 */
export function parseRevealTarget(event: Event, fallbackSlug?: string): RevealTarget | null {
  const detail = (event as CustomEvent).detail || {}
  const path = typeof detail.path === 'string' ? detail.path : ''
  if (!path) return null
  const projectSlug = typeof detail.projectSlug === 'string' && detail.projectSlug
    ? detail.projectSlug
    : fallbackSlug
  if (!projectSlug) return null
  return {
    projectSlug,
    path,
    pathKind: detail.pathKind === 'root' || detail.pathKind === 'directory' ? detail.pathKind : 'file',
    rootSide: detail.rootSide === 'container' ? 'container' : 'virtual',
  }
}
