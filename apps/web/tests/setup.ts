import '@testing-library/jest-dom/vitest'

// Keep tests deterministic and avoid Node's experimental file-backed localStorage.
const values = new Map<string, string>()
const storage: Storage = {
  get length() { return values.size },
  clear: () => values.clear(),
  getItem: key => values.get(key) ?? null,
  key: index => Array.from(values.keys())[index] ?? null,
  removeItem: key => { values.delete(key) },
  setItem: (key, value) => { values.set(key, String(value)) },
}
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage })

// jsdom has no EventSource; screens that subscribe to session SSE need a stub.
if (typeof globalThis.EventSource === 'undefined') {
  class StubEventSource {
    static CONNECTING = 0
    static OPEN = 1
    static CLOSED = 2
    readonly CONNECTING = 0
    readonly OPEN = 1
    readonly CLOSED = 2
    readyState = StubEventSource.CONNECTING
    url: string
    withCredentials: boolean
    onopen: ((this: EventSource, ev: Event) => unknown) | null = null
    onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null
    onerror: ((this: EventSource, ev: Event) => unknown) | null = null
    constructor(url: string | URL, init?: EventSourceInit) {
      this.url = String(url)
      this.withCredentials = Boolean(init?.withCredentials)
      this.readyState = StubEventSource.OPEN
    }
    close() { this.readyState = StubEventSource.CLOSED }
    addEventListener() {}
    removeEventListener() {}
    dispatchEvent() { return false }
  }
  Object.defineProperty(globalThis, 'EventSource', {
    configurable: true,
    writable: true,
    value: StubEventSource,
  })
}
