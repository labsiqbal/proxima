import React from 'react'
import { listReferenceFiles } from '../api/files'
import type { MentionItem } from '../components/ui/MentionTextarea'

type MentionState = {
  token: string
  slug?: string
  items: MentionItem[]
}

/** Project-file choices shared by every @-mention surface.
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
    void listReferenceFiles(token, slug)
      .then(body => {
        if (seq !== requestSeq.current) return
        setState({ token, slug, items: body.files.map(file => ({ path: file.path })) })
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
