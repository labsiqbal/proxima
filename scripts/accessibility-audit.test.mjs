import assert from 'node:assert/strict'
import test from 'node:test'
import {
  GATE_TEXT_STYLES,
  REDACTED_TAILSCALE_PROVENANCE,
  remoteRequestPolicy,
  resolvePrivateTailscaleEntry,
  summarizeStaticShellRequests,
} from './accessibility-audit-policy.mjs'

const serveStatus = {
  Web: {
    'device.example.ts.net:443': {
      Handlers: {
        '/': { Proxy: 'http://127.0.0.1:8765' },
      },
    },
  },
  TCP: {
    443: { HTTPS: true },
  },
}

const deviceStatus = {
  Self: {
    DNSName: 'device.example.ts.net.',
    TailscaleIPs: ['100.64.0.10', 'fd7a:115c:a1e0::10'],
  },
}

test('correlates an override with the current device Serve entry', () => {
  const entry = resolvePrivateTailscaleEntry({
    serveStatus,
    deviceStatus,
    configuredBase: 'https://device.example.ts.net/',
    configuredAddress: '100.64.0.10',
  })

  assert.equal(entry.url, 'https://device.example.ts.net/')
  assert.equal(entry.address, '100.64.0.10')
  assert.deepEqual(entry.provenance, REDACTED_TAILSCALE_PROVENANCE)
  assert.doesNotMatch(JSON.stringify(entry.provenance), /device\.example|100\.64/)
})

test('rejects a false override origin and address', () => {
  assert.throws(
    () => resolvePrivateTailscaleEntry({
      serveStatus,
      deviceStatus,
      configuredBase: 'https://other.example.ts.net/',
    }),
    /must match the current device Tailscale Serve origin/,
  )
  assert.throws(
    () => resolvePrivateTailscaleEntry({
      serveStatus,
      deviceStatus,
      configuredAddress: '100.64.0.11',
    }),
    /must match the current Tailscale device/,
  )
})

test('requires one root Serve proxy for the configured local service', () => {
  assert.throws(
    () => resolvePrivateTailscaleEntry({
      serveStatus: {
        Web: {
          'device.example.ts.net:443': {
            Handlers: {
              '/': { Proxy: 'http://127.0.0.1:9999' },
            },
          },
        },
        TCP: { 443: { HTTPS: true } },
      },
      deviceStatus,
    }),
    /Expected one Tailscale root entry/,
  )
})

test('binds a renamed device through its current DNS and Serve port', () => {
  const entry = resolvePrivateTailscaleEntry({
    serveStatus: {
      Web: {
        'serve-alias.example.ts.net:443': {
          Handlers: {
            '/': { Proxy: 'http://127.0.0.1:8765' },
          },
        },
      },
      TCP: { 443: { HTTPS: true } },
    },
    deviceStatus,
  })

  assert.equal(entry.url, 'https://device.example.ts.net/')
  assert.equal(entry.address, '100.64.0.10')
})

test('forwards static shell files and traps every live data request', () => {
  const origin = 'https://device.example.ts.net'
  const cases = [
    ['GET', '/', 'forward'],
    ['GET', '/assets/app.js', 'forward'],
    ['GET', '/icons/icon.svg', 'forward'],
    ['GET', '/@vite/client', 'forward'],
    ['GET', '/@react-refresh', 'forward'],
    ['GET', '/@id/react', 'forward'],
    ['GET', '/src/main.tsx', 'forward'],
    ['GET', '/node_modules/.vite/deps/react.js', 'forward'],
    ['GET', '/node_modules/vite/dist/client/env.mjs', 'forward'],
    ['GET', '/manifest.webmanifest', 'forward'],
    ['GET', '/sw.js', 'forward'],
    ['GET', '/api/config', 'fulfill'],
    ['GET', '/api/setup/status', 'fulfill'],
    ['POST', '/auth/resume', 'fulfill'],
    ['GET', '/api/projects', 'block'],
    ['POST', '/auth/login', 'block'],
    ['GET', '/health', 'block'],
    ['GET', '/@fs/private/path', 'block'],
    ['GET', '/node_modules/other-package/index.js', 'block'],
  ]

  for (const [method, pathname, action] of cases) {
    assert.equal(
      remoteRequestPolicy({ method, url: `${origin}${pathname}` }, origin).action,
      action,
      `${method} ${pathname}`,
    )
  }
  assert.equal(
    remoteRequestPolicy(
      { method: 'GET', url: 'https://fonts.example/style.css' },
      origin,
    ).action,
    'block',
  )
})

test('requires exactly one remote shell root GET', () => {
  assert.deepEqual(
    summarizeStaticShellRequests(['GET /', 'GET /assets/app.js', 'GET /manifest.webmanifest']),
    {
      rootGetCount: 1,
      staticGetCount: 3,
    },
  )
  assert.throws(
    () => summarizeStaticShellRequests(['GET /assets/app.js']),
    /exactly one unauthenticated root GET/,
  )
  assert.throws(
    () => summarizeStaticShellRequests(['GET /', 'GET /']),
    /exactly one unauthenticated root GET/,
  )
})

test('owns the complete password-gate text style matrix', () => {
  assert.deepEqual(GATE_TEXT_STYLES, [
    'title',
    'subtitle',
    'inputValue',
    'placeholder',
    'error',
    'button',
  ])
})
