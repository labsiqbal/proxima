// Global error surface - the store behind the app-wide error toasts.
//
// Why: the app's worst failure mode is silent. A throw inside an event handler,
// a rejected promise nobody awaited, a hashed chunk that 404s after a redeploy,
// or a fetch that dies in a `catch {}` all look identical to the owner: the
// click does nothing. This module turns every one of those into a visible,
// copy-pasteable report (see docs/PRUNE-SPEC.md B4, evidence issue #115).
//
// Deliberately framework-free: `apps/web/src/api/client.ts` and `main.tsx` feed
// it, `components/shell/AppErrorToasts.tsx` renders it via useSyncExternalStore.

export type AppErrorKind = 'error' | 'rejection' | 'chunk' | 'api'

export interface AppErrorEntry {
  id: number
  /** Dedupe identity - repeats of the same key bump `count` instead of stacking. */
  key: string
  kind: AppErrorKind
  title: string
  body: string
  /** Message + stack snippet, for pasting into a bug report. */
  detail: string
  /** Stale-chunk failures: reloading is the actual fix, so offer it. */
  suggestReload: boolean
  count: number
}

/** Hard ceiling on visible toasts - an error storm must never bury the app. */
export const APP_ERROR_LIMIT = 3
const DETAIL_STACK_LINES = 8
const BODY_MAX = 300
const KEY_MAX = 160
const UNREACHABLE_PREFIX = 'api:0:'

// A failed dynamic import is a deploy-seam failure, not a code bug: the tab
// holds an index that points at chunks the new build no longer serves.
const CHUNK_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /unable to preload css/i,
  /chunkloaderror/i,
  /loading chunk \S+ failed/i,
]

// Browser noise and deliberate cancellations. Surfacing these would train the
// owner to ignore the toast, which defeats the whole point.
const IGNORED_PATTERNS = [
  /resizeobserver loop/i,
  /\baborted\b/i,
  /\babort(ed)? a request\b/i,
  /^script error\.?$/i,
]

let entries: readonly AppErrorEntry[] = []
let nextId = 1
const listeners = new Set<() => void>()

const emit = () => { listeners.forEach(listener => listener()) }

export function subscribeAppErrors(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** Current entries. Reference-stable until something actually changes. */
export function appErrorSnapshot(): readonly AppErrorEntry[] {
  return entries
}

export function dismissAppError(id: number): void {
  const next = entries.filter(entry => entry.id !== id)
  if (next.length === entries.length) return
  entries = next
  emit()
}

export function clearAppErrors(): void {
  if (!entries.length) return
  entries = []
  emit()
}

const errorName = (cause: unknown): string =>
  cause instanceof Error && cause.name ? cause.name : 'Error'

const errorMessage = (cause: unknown): string => {
  if (cause instanceof Error) return cause.message || String(cause)
  if (typeof cause === 'string') return cause
  if (cause && typeof cause === 'object') {
    const message = (cause as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
    try { return JSON.stringify(cause) } catch { return String(cause) }
  }
  return String(cause)
}

const truncate = (text: string, max: number) =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text

const isIgnorable = (cause: unknown, message: string): boolean => {
  if (!message.trim()) return true
  if (errorName(cause) === 'AbortError') return true
  return IGNORED_PATTERNS.some(pattern => pattern.test(message))
}

const isChunkFailure = (message: string) => CHUNK_PATTERNS.some(pattern => pattern.test(message))

const buildDetail = (cause: unknown, message: string): string => {
  const head = `${errorName(cause)}: ${message}`
  const stack = cause instanceof Error && typeof cause.stack === 'string' ? cause.stack : ''
  const frames = stack
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.startsWith('at '))
    .slice(0, DETAIL_STACK_LINES)
  return [head, ...frames].join('\n')
}

/**
 * Where a toast goes to be remembered (#158). The toast itself is ephemeral by
 * design, but its diagnostic is exactly what the Inbox exists to keep, so a
 * *new* failure is also filed there. Repeats are not: they already collapse
 * into one toast, and the ledger keys on the same identity.
 *
 * Installed by the app once a session token exists; unset means "nowhere to
 * file it", which is the correct state on the auth screen.
 */
let archive: ((entry: Omit<AppErrorEntry, 'id' | 'count'>) => void) | null = null

export function setErrorArchive(
  sink: ((entry: Omit<AppErrorEntry, 'id' | 'count'>) => void) | null,
): void {
  archive = sink
}

function push(entry: Omit<AppErrorEntry, 'id' | 'count'>): void {
  if (unloading) return
  const existing = entries.find(item => item.key === entry.key)
  if (existing) {
    entries = entries.map(item => item.key === entry.key ? { ...item, count: item.count + 1 } : item)
    emit()
    return
  }
  const next = [...entries, { ...entry, id: nextId++, count: 1 }]
  // Newest failures win: the one that just broke is the one worth reading.
  entries = next.slice(-APP_ERROR_LIMIT)
  emit()
  // After emit, and never allowed to throw: filing the record must not be able
  // to break the surface that reports breakage.
  try { archive?.(entry) } catch { /* the toast is the fallback */ }
}

/**
 * Report an uncaught error or rejection. `kind` describes where it came from;
 * a stale-chunk failure is re-classified to `chunk` because its fix differs.
 */
export function reportAppError(kind: 'error' | 'rejection', cause: unknown): void {
  const message = errorMessage(cause)
  if (isIgnorable(cause, message)) return
  const detail = buildDetail(cause, message)
  if (isChunkFailure(message)) {
    push({
      key: 'chunk',
      kind: 'chunk',
      title: 'Proxima was updated',
      body: 'This tab is still running the previous build, so part of the app failed to load. Reload to pick up the new one.',
      detail,
      suggestReload: true,
    })
    return
  }
  push({
    key: truncate(`${kind}:${message}`, KEY_MAX),
    kind,
    title: kind === 'rejection' ? 'A background task failed' : 'Something went wrong',
    body: truncate(message, BODY_MAX),
    detail,
    suggestReload: false,
  })
}

export interface ApiFailure {
  status: number
  method?: string
  path?: string
  message: string
}

/**
 * Report an API call that failed for reasons no screen can meaningfully render.
 *
 * Only transport failures (`status === 0`, server unreachable) and 5xx are
 * surfaced globally. 4xx - validation, governance refusals, not-found - belong
 * to the flow that made the call and already have their own error states; a
 * global toast there would be a duplicate, not a diagnosis.
 */
export function reportApiFailure(failure: ApiFailure): void {
  const { status, message } = failure
  if (status !== 0 && status < 500) return
  if (isIgnorable(null, message)) return
  const method = failure.method || 'GET'
  const path = failure.path || 'the server'
  push({
    key: truncate(`api:${status}:${method} ${path}`, KEY_MAX),
    kind: 'api',
    title: status === 0 ? 'Could not reach Proxima' : 'The server failed a request',
    body: status === 0
      ? `${method} ${path} never got a response. Check that the Proxima server is still running, then retry.`
      : `${method} ${path} failed with ${status}. The action did not go through.`,
    detail: truncate(message, 1000),
    suggestReload: false,
  })
}

/**
 * A successful response proves the server is reachable again, so the
 * "could not reach Proxima" toasts are reporting a condition that has ended.
 * Leaving them up would train the owner to ignore the surface. Failures the
 * server itself produced (5xx) and real code errors are left alone.
 */
export function noteApiSuccess(): void {
  const next = entries.filter(entry => !entry.key.startsWith(UNREACHABLE_PREFIX))
  if (next.length === entries.length) return
  entries = next
  emit()
}

let installedTarget: Window | null = null
let installCount = 0
let detach: (() => void) | null = null
// A reload or navigation aborts every in-flight request. Those failures are the
// owner leaving, not a bug - reporting them would flash a toast on every reload.
let unloading = false

/**
 * Attach the window-level handlers. Idempotent and ref-counted: `main.tsx`
 * installs early (so boot-time throws are caught before React mounts) and the
 * toast component installs again on mount without doubling every report.
 */
export function installGlobalErrorHandlers(target: Window = window): () => void {
  if (installedTarget && installedTarget !== target) {
    detach?.()
    detach = null
    installCount = 0
    installedTarget = null
  }
  if (!installedTarget) {
    const onError = (event: ErrorEvent) => { reportAppError('error', event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { reportAppError('rejection', event.reason) }
    const onUnload = () => { unloading = true }
    target.addEventListener('error', onError as EventListener)
    target.addEventListener('unhandledrejection', onRejection as EventListener)
    target.addEventListener('pagehide', onUnload)
    target.addEventListener('beforeunload', onUnload)
    installedTarget = target
    detach = () => {
      unloading = false
      target.removeEventListener('error', onError as EventListener)
      target.removeEventListener('unhandledrejection', onRejection as EventListener)
      target.removeEventListener('pagehide', onUnload)
      target.removeEventListener('beforeunload', onUnload)
    }
  }
  installCount += 1
  let released = false
  return () => {
    if (released) return
    released = true
    installCount -= 1
    if (installCount > 0) return
    detach?.()
    detach = null
    installedTarget = null
  }
}
