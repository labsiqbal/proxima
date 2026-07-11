export class ApiError extends Error {
  status: number
  path?: string
  method?: string
  constructor(status: number, message: string, path?: string, method?: string) {
    super(message)
    this.status = status
    this.path = path
    this.method = method
  }
}

export async function api<T>(path: string, token?: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...((options.headers as Record<string, string>) || {}) }
  if (token) headers.Authorization = `Bearer ${token}`
  const method = (options.method || 'GET').toUpperCase()
  let res: Response
  try {
    res = await fetch(path, { ...options, headers })
  } catch (err) {
    throw new ApiError(0, `${method} ${path} failed: ${err instanceof Error ? err.message : String(err)}`, path, method)
  }
  if (!res.ok) {
    const text = await res.text()
    let message = text || res.statusText
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown }
      const detail = parsed?.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (Array.isArray(detail)) {
        // FastAPI validation errors: [{loc, msg, ...}] — surface the messages.
        message = detail.map((d: { msg?: string }) => d?.msg || JSON.stringify(d)).join('; ')
      } else if (detail && typeof detail === 'object') {
        message = JSON.stringify(detail)
      } else if (typeof parsed?.message === 'string') {
        message = parsed.message
      }
    } catch {
      /* not JSON — keep the raw text */
    }
    throw new ApiError(res.status, `${method} ${path} failed (${res.status}): ${message}`, path, method)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}
