import React from 'react'
import { listArtifacts, listReferenceFiles, type Artifact } from '../api/files'
import type { MentionItem } from '../components/ui/MentionTextarea'

type MentionState = {
  token: string
  slug?: string
  items: MentionItem[]
}

/** How far back the artifact scan looks for @-mention choices.
 *
 * The project-artifacts endpoint is a recency window (not a full registry page).
 * A year keeps finished deliverables reachable in daily chat without pulling the
 * whole disk; the scanner still caps the payload.
 */
const MENTION_ARTIFACT_SINCE_MINUTES = 60 * 24 * 365

/** Merge path-only reference files with typed produced artifacts.
 *
 * Artifacts win on path collision (they carry title + kind). Artifacts are listed
 * first so typing bare `@` surfaces recent deliverables ahead of the full file tree.
 */
export function mergeProjectMentionItems(
  files: { path: string }[],
  artifacts: Pick<Artifact, 'path' | 'title' | 'type'>[],
): MentionItem[] {
  const artifactItems: MentionItem[] = []
  const seen = new Set<string>()
  for (const artifact of artifacts) {
    const path = artifact.path?.trim()
    if (!path || seen.has(path)) continue
    seen.add(path)
    artifactItems.push({
      path,
      title: artifact.title || undefined,
      type: artifact.type || undefined,
    })
  }
  const fileItems: MentionItem[] = []
  for (const file of files) {
    const path = file.path?.trim()
    if (!path || seen.has(path)) continue
    seen.add(path)
    fileItems.push({ path })
  }
  return [...artifactItems, ...fileItems]
}

/** Project-file + produced-artifact choices shared by every @-mention surface.
 *
 * Responses are scoped to the token/project pair that started them, so a slow old
 * project cannot replace a newer selection. File mutations trigger a best-effort
 * refresh while keeping the last good list visible until the response arrives.
 */
export function useProjectMentionItems(token: string, slug?: string): MentionItem[] {
  const [state, setState] = React.useState<MentionState>({ token: '', items: [] })
  const requestSeq = React.useRef(0)

  const load = React.useCallback(() => {
    const seq = ++requestSeq.current
    if (!token || !slug) {
      setState({ token, slug, items: [] })
      return
    }
    void Promise.all([
      listReferenceFiles(token, slug),
      listArtifacts(token, slug, MENTION_ARTIFACT_SINCE_MINUTES).catch(() => ({ artifacts: [] as Artifact[] })),
    ])
      .then(([filesBody, artifactsBody]) => {
        if (seq !== requestSeq.current) return
        setState({
          token,
          slug,
          items: mergeProjectMentionItems(filesBody.files, artifactsBody.artifacts),
        })
      })
      .catch(() => {
        if (seq !== requestSeq.current) return
        setState(current => current.token === token && current.slug === slug
          ? current
          : { token, slug, items: [] })
      })
  }, [token, slug])

  React.useEffect(() => {
    load()
    const refresh = () => load()
    window.addEventListener('proxima:files-changed', refresh)
    return () => {
      requestSeq.current += 1
      window.removeEventListener('proxima:files-changed', refresh)
    }
  }, [load])

  return state.token === token && state.slug === slug ? state.items : []
}
