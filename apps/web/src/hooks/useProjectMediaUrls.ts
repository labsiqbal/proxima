import React from 'react'
import { fetchRawBlob } from '../api/files'
import {
  mergeProjectMediaRefs,
  projectMediaKey,
  type ProjectMediaRef,
} from '../api/projectMedia'

function revokeObjectUrl(url: string) {
  if (typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url)
}

function refsSignature(refs: ProjectMediaRef[]): string {
  return mergeProjectMediaRefs(refs)
    .map(ref => projectMediaKey(ref.src, ref.target))
    .sort()
    .join('\n')
}

/** Hydrate authenticated object URLs for target-bound and SVG project media. */
export function useProjectMediaUrls(
  token: string | undefined,
  slug: string | undefined,
  refs: ProjectMediaRef[],
): Record<string, string> {
  const [urls, setUrls] = React.useState<Record<string, string>>({})
  const signature = refsSignature(refs)
  const refsRef = React.useRef(refs)
  refsRef.current = refs

  React.useEffect(() => {
    let alive = true
    const created: string[] = []
    setUrls({})
    const unique = mergeProjectMediaRefs(refsRef.current)
    if (!token || !slug || !unique.length) {
      return () => {
        alive = false
      }
    }
    void Promise.all(unique.map(async ref => {
      try {
        const url = await fetchRawBlob(token, slug, ref.src, ref.target)
        if (!alive) {
          revokeObjectUrl(url)
          return null
        }
        created.push(url)
        return [projectMediaKey(ref.src, ref.target), url] as const
      } catch {
        return null
      }
    })).then(entries => {
      if (!alive) return
      setUrls(Object.fromEntries(
        entries.filter((entry): entry is readonly [string, string] => entry != null),
      ))
    })
    return () => {
      alive = false
      created.forEach(url => revokeObjectUrl(url))
    }
  }, [token, slug, signature])

  return urls
}
