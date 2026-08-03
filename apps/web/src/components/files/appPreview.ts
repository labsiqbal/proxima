import { appViewUrl, type AppStatus } from '../../api/files'

// The app-preview security model, stated once so both halves of Run & Preview
// mean the same thing (#147). The Run controls live in the right dock and the
// viewport renders in the Artifacts main window, but WHERE the frame is drawn
// changed - what it is allowed to do did not.
//
// A managed dev server is a real application, not a previewed document: it gets
// scripts, forms, popups, and modals. `allow-same-origin` is granted ONLY when
// the frame is loaded from an origin that is not Proxima's - the per-app
// preview subdomain or the loopback/tailnet relay port. The same-origin
// `/api/appview/<slug>/` proxy is the fallback for deployments that have
// neither, and there the sandbox must stay opaque: the page holds the owner's
// session, so a framed dev server that could reach it is exactly the
// session-theft path ADR-0042 refuses.
//
// Both strings are exported (and asserted in tests) rather than assembled at
// the call site, so a future edit to the viewport cannot widen the sandbox by
// accident.
export const APP_FRAME_SANDBOX_ISOLATED = 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals'
export const APP_FRAME_SANDBOX_PROXIED = 'allow-scripts allow-forms allow-popups allow-modals'

/** The sandbox an app frame gets, from whether its origin is Proxima's own. */
export const appFrameSandbox = (isolatedOrigin: boolean) =>
  isolatedOrigin ? APP_FRAME_SANDBOX_ISOLATED : APP_FRAME_SANDBOX_PROXIED

export type AppPreviewOrigin = {
  /** What the frame loads (before the cache-busting nonce is appended). */
  baseUrl: string
  /** What "Open in new tab" points at - the same origin, no nonce. */
  openUrl: string
  /** True when the frame is NOT on Proxima's origin. Decides the sandbox. */
  isolatedOrigin: boolean
}

/**
 * Prefer an isolated origin: the remote apps-domain subdomain when configured,
 * else the app's preview relay port on the same host (local and remote).
 * Absolute asset paths and HMR websockets work on that origin, gated by the
 * `proxima_preview` cookie (host-scoped cookies ignore ports) and
 * credential-stripped upstream.
 *
 * The relay speaks plain HTTP only. From an https page (Tailscale Serve, a
 * fronting proxy) an `https://host:relayPort` iframe dies in the TLS handshake
 * and an http:// one is blocked as mixed content - so https origins skip the
 * relay and use the same-origin `/api/appview` proxy, which rides the page's
 * own TLS.
 */
export function appPreviewOrigin(
  slug: string,
  status: AppStatus,
  appsDomain: string | null,
  page: { hostname: string; protocol: string } = location,
): AppPreviewOrigin {
  const isRemote = page.hostname !== 'localhost' && page.hostname !== '127.0.0.1'
  const subdomainUrl = appsDomain && isRemote && status.running && status.ready
    ? `${page.protocol}//preview-${slug}.${appsDomain}/`
    : ''
  const relayUrl = !subdomainUrl && status.running && status.preview_port && page.protocol === 'http:'
    ? `http://${page.hostname}:${status.preview_port}/`
    : ''
  const baseUrl = subdomainUrl || relayUrl || appViewUrl(slug)
  return { baseUrl, openUrl: baseUrl, isolatedOrigin: Boolean(subdomainUrl || relayUrl) }
}
