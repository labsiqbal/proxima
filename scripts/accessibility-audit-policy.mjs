import assert from 'node:assert/strict'
import net from 'node:net'

export const GATE_TEXT_STYLES = Object.freeze([
  'title',
  'subtitle',
  'inputValue',
  'placeholder',
  'error',
  'button',
])

export const REDACTED_TAILSCALE_PROVENANCE = Object.freeze({
  device: 'current Tailscale device (redacted)',
  serve: 'root handler for configured local Proxima service',
  origin: 'private Tailscale origin (redacted)',
})

const staticPaths = new Set([
  '/',
  '/@react-refresh',
  '/@vite/client',
  '/manifest.webmanifest',
  '/sw.js',
])

const staticPathPrefixes = [
  '/@id/',
  '/assets/',
  '/icons/',
  '/node_modules/.vite/',
  '/node_modules/vite/dist/client/',
  '/src/',
]

export function privateEntryUrl(value) {
  let url
  try {
    url = new URL(value)
  } catch {
    throw new Error('Private Tailscale entry must be an absolute URL')
  }
  assert(['http:', 'https:'].includes(url.protocol), 'Private Tailscale entry must use HTTP or HTTPS')
  assert(!url.username && !url.password, 'Private Tailscale entry must not contain credentials')
  url.pathname = '/'
  url.search = ''
  url.hash = ''
  return url.toString()
}

function servedAuthority(origin) {
  const value = String(origin)
  const url = new URL(value.includes('://') ? value : `https://${value}`)
  return {
    hostname: url.hostname.replace(/\.$/, ''),
    port: value.match(/:(\d+)$/)?.[1] || (url.protocol === 'http:' ? '80' : '443'),
  }
}

export function resolvePrivateTailscaleEntry({
  serveStatus,
  deviceStatus,
  proxyPort = '8765',
  configuredBase = '',
  configuredAddress = '',
}) {
  assert(/^\d+$/.test(proxyPort), 'PROXIMA_A11Y_REMOTE_PROXY_PORT must be a port number')
  const candidates = Object.entries(serveStatus?.Web || {}).filter(([, config]) => {
    const proxy = config?.Handlers?.['/']?.Proxy
    if (typeof proxy !== 'string') return false
    try {
      const target = new URL(proxy)
      const targetPort = target.port || (target.protocol === 'https:' ? '443' : '80')
      return ['127.0.0.1', 'localhost', '::1'].includes(target.hostname)
        && targetPort === proxyPort
    } catch {
      return false
    }
  })
  assert.equal(candidates.length, 1, 'Expected one Tailscale root entry for the current Proxima service')

  const served = servedAuthority(candidates[0][0])
  const protocol = serveStatus?.TCP?.[served.port]?.HTTPS ? 'https' : 'http'
  const hostname = String(deviceStatus?.Self?.DNSName || '').replace(/\.$/, '')
  assert(hostname, 'Current Tailscale device has no DNS name')
  const currentAddress = deviceStatus?.Self?.TailscaleIPs?.find(candidate => net.isIP(candidate) === 4)
  assert(currentAddress, 'Current Tailscale device has no IPv4 address')

  const defaultPort = protocol === 'https' ? '443' : '80'
  const authority = served.port === defaultPort
    ? hostname
    : `${hostname}:${served.port}`
  const discoveredUrl = privateEntryUrl(`${protocol}://${authority}`)
  const requestedUrl = configuredBase ? privateEntryUrl(configuredBase) : discoveredUrl
  assert.equal(
    new URL(requestedUrl).origin,
    new URL(discoveredUrl).origin,
    'PROXIMA_A11Y_REMOTE_BASE must match the current device Tailscale Serve origin',
  )

  if (configuredAddress) {
    assert.equal(net.isIP(configuredAddress), 4, 'PROXIMA_A11Y_REMOTE_ADDRESS must be an IPv4 address')
    assert.equal(
      configuredAddress,
      currentAddress,
      'PROXIMA_A11Y_REMOTE_ADDRESS must match the current Tailscale device',
    )
  }

  return {
    url: requestedUrl,
    address: configuredAddress || currentAddress,
    provenance: REDACTED_TAILSCALE_PROVENANCE,
  }
}

function bootstrapResponse(method, pathname) {
  if (method === 'GET' && pathname === '/api/config') {
    return {
      status: 200,
      body: {
        apps_domain: null,
        features: {
          design_studio: false,
          workflow_graph: false,
          master_orchestrator: false,
        },
      },
    }
  }
  if (method === 'GET' && pathname === '/api/setup/status') {
    return {
      status: 200,
      body: {
        bootstrap_required: false,
        single_user: true,
        mode: 'single',
        password_set: true,
        hermes_profiles_root: '',
        runners: [],
      },
    }
  }
  if (method === 'POST' && pathname === '/auth/resume') {
    return {
      status: 401,
      body: { detail: 'Not authenticated' },
    }
  }
  return null
}

export function remoteRequestPolicy(request, targetOrigin) {
  let url
  try {
    url = new URL(request.url)
  } catch {
    return { action: 'block', label: 'invalid URL' }
  }
  const method = String(request.method || 'GET').toUpperCase()
  if (url.origin !== targetOrigin) {
    return { action: 'block', label: `${method} cross-origin resource` }
  }

  const bootstrap = bootstrapResponse(method, url.pathname)
  if (bootstrap) {
    return {
      action: 'fulfill',
      label: `${method} ${url.pathname}`,
      response: bootstrap,
    }
  }

  if (method !== 'GET') {
    return { action: 'block', label: `${method} same-origin request` }
  }
  if (
    staticPaths.has(url.pathname)
    || staticPathPrefixes.some(prefix => url.pathname.startsWith(prefix))
  ) {
    return { action: 'forward', label: `GET ${url.pathname}` }
  }
  return { action: 'block', label: `GET ${url.pathname}` }
}
