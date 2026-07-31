export class ApiError extends Error {
  status: number
  path?: string
  method?: string
  field?: string
  detail?: string
  constructor(status: number, message: string, path?: string, method?: string, field?: string, detail?: string) {
    super(message)
    this.status = status
    this.path = path
    this.method = method
    this.field = field
    this.detail = detail
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
    let field: string | undefined
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown }
      const detail = parsed?.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (Array.isArray(detail)) {
        const validation = detail as { loc?: unknown; msg?: unknown }[]
        message = validation.map(d => typeof d?.msg === 'string' ? d.msg : JSON.stringify(d)).join('; ')
        const location = validation[0]?.loc
        if (Array.isArray(location)) {
          field = [...location].reverse().find(value => typeof value === 'string' && value !== 'body') as string | undefined
        }
      } else if (detail && typeof detail === 'object') {
        const structured = detail as { message?: unknown; field?: unknown }
        message = typeof structured.message === 'string' ? structured.message : JSON.stringify(detail)
        field = typeof structured.field === 'string' ? structured.field : undefined
      } else if (typeof parsed?.message === 'string') {
        message = parsed.message
      }
    } catch {
      /* not JSON — keep the raw text */
    }
    throw new ApiError(res.status, `${method} ${path} failed (${res.status}): ${message}`, path, method, field, message)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}
