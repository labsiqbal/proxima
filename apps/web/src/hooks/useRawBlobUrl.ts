import React from 'react'
import { fetchRawBlob } from '../api/files'
import type { FileTarget } from '../types'

function targetKey(target?: FileTarget): string {
  if (!target) return ''
  return `${target.project}:${target.area.kind}:${target.area.id ?? 'root'}:${target.path}`
}

function revokeObjectUrl(url: string) {
  if (typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url)
}

export function useRawBlobUrl(
  token: string | undefined,
  slug: string | undefined,
  path: string,
  target?: FileTarget,
): string | null {
  const [url, setUrl] = React.useState<string | null>(null)
  const targetIdentity = targetKey(target)
  const targetRef = React.useRef(target)
  targetRef.current = target

  React.useEffect(() => {
    if (!token || !slug || !path) {
      setUrl(null)
      return
    }
    let alive = true
    let objectUrl: string | null = null
    setUrl(null)
    fetchRawBlob(token, slug, path, targetRef.current)
      .then(next => {
        if (!alive) {
          revokeObjectUrl(next)
          return
        }
        objectUrl = next
        setUrl(next)
      })
      .catch(() => {
        if (alive) setUrl(null)
      })
    return () => {
      alive = false
      if (objectUrl) revokeObjectUrl(objectUrl)
    }
  }, [token, slug, path, targetIdentity])

  return url
}
