import { describe, expect, it } from 'vitest'
import { APP_FRAME_SANDBOX_ISOLATED, APP_FRAME_SANDBOX_PROXIED, appFrameSandbox, appPreviewOrigin } from './appPreview'
import type { AppStatus } from '../../api/files'

const ready = (extra: Partial<AppStatus> = {}): AppStatus => ({
  state: 'ready',
  running: true,
  ready: true,
  port: 5180,
  ...extra,
} as AppStatus)

// The viewport moved to the main window in #147; the sandbox model did not move
// with it. These are the exact strings the docked frame carried, pinned so a
// change to either has to be deliberate.
describe('app frame sandbox', () => {
  it('grants an isolated origin scripts and same-origin, and nothing more', () => {
    expect(APP_FRAME_SANDBOX_ISOLATED).toBe('allow-scripts allow-same-origin allow-forms allow-popups allow-modals')
    expect(appFrameSandbox(true)).toBe(APP_FRAME_SANDBOX_ISOLATED)
  })

  it('never grants same-origin to a frame served from Proxima itself', () => {
    expect(APP_FRAME_SANDBOX_PROXIED).toBe('allow-scripts allow-forms allow-popups allow-modals')
    expect(APP_FRAME_SANDBOX_PROXIED).not.toContain('allow-same-origin')
    expect(appFrameSandbox(false)).toBe(APP_FRAME_SANDBOX_PROXIED)
  })
})

describe('app preview origin', () => {
  it('prefers the per-app subdomain when one is configured on a remote origin', () => {
    const origin = appPreviewOrigin('demo', ready(), 'apps.example.com', { hostname: '100.1.2.3', protocol: 'https:' })
    expect(origin.baseUrl).toBe('https://preview-demo.apps.example.com/')
    expect(origin.isolatedOrigin).toBe(true)
  })

  it('uses the relay port on a plain-http origin, local or tailnet', () => {
    for (const hostname of ['localhost', '100.101.102.103']) {
      const origin = appPreviewOrigin('demo', ready({ preview_port: 43123 }), null, { hostname, protocol: 'http:' })
      expect(origin.baseUrl).toBe(`http://${hostname}:43123/`)
      expect(origin.isolatedOrigin).toBe(true)
    }
  })

  it('falls back to the same-origin appview proxy on an https origin, unsandboxed for same-origin', () => {
    // An https page cannot frame the plain-http relay: TLS fails, and http is
    // mixed content. The proxy rides the page's own TLS - and pays for it with
    // an opaque sandbox, because that origin holds the owner's session.
    const origin = appPreviewOrigin('demo', ready({ preview_port: 43123 }), null, { hostname: 'linc.example.ts.net', protocol: 'https:' })
    expect(origin.baseUrl).toBe('/api/appview/demo/')
    expect(origin.isolatedOrigin).toBe(false)
    expect(appFrameSandbox(origin.isolatedOrigin)).not.toContain('allow-same-origin')
  })

  it('falls back to the proxy when no relay port has been published yet', () => {
    const origin = appPreviewOrigin('demo', ready(), null, { hostname: 'localhost', protocol: 'http:' })
    expect(origin.baseUrl).toBe('/api/appview/demo/')
    expect(origin.isolatedOrigin).toBe(false)
  })
})
