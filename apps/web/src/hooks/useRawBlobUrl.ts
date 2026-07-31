import React from 'react'
import { fetchRawBlob } from '../api/files'
import type { FileTarget } from '../types'

export type RawBlobStatus = 'idle' | 'loading' | 'ready' | 'error'

export type RawBlobState = {
  url: string | null
  status: RawBlobStatus
  retry: () => void
}

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
): RawBlobState {
  const [url, setUrl] = React.useState<string | null>(null)
  const [status, setStatus] = React.useState<RawBlobStatus>('idle')
  const [attempt, setAttempt] = React.useState(0)
  const targetIdentity = targetKey(target)
  const targetRef = React.useRef(target)
  targetRef.current = target

  const retry = React.useCallback(() => {
    setAttempt(current => current + 1)
  }, [])

  React.useEffect(() => {
    if (!token || !slug || !path) {
      setUrl(null)
      setStatus('idle')
      return
    }
    let alive = true
    let objectUrl: string | null = null
    setUrl(null)
    setStatus('loading')
    fetchRawBlob(token, slug, path, targetRef.current)
      .then(next => {
        if (!alive) {
          revokeObjectUrl(next)
          return
        }
        objectUrl = next
        setUrl(next)
        setStatus('ready')
      })
      .catch(() => {
        if (!alive) return
        setUrl(null)
        setStatus('error')
      })
    return () => {
      alive = false
      if (objectUrl) revokeObjectUrl(objectUrl)
    }
  }, [token, slug, path, targetIdentity, attempt])

  return { url, status, retry }
}
