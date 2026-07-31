import assert from 'node:assert/strict'
import test from 'node:test'
import {
  assertServiceWorkerCacheMatrix,
  GATE_TEXT_STYLES,
  REDACTED_TAILSCALE_PROVENANCE,
  remoteRequestPolicy,
  resolvePrivateTailscaleEntry,
  summarizeStaticShellRequests,
} from './accessibility-audit-policy.mjs'
import { RemoteEntryInterceptor } from './remote-entry-interceptor.mjs'

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
    ['GET', '/@vite/client', 'fulfill'],
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
  const viteClient = remoteRequestPolicy(
    { method: 'GET', url: `${origin}/@vite/client` },
    origin,
  )
  assert.doesNotMatch(viteClient.response.body, /WebSocket/)
  assert.match(viteClient.response.body, /createHotContext/)
  assert.match(viteClient.response.body, /updateStyle/)
  assert.equal(
    remoteRequestPolicy(
      { method: 'GET', url: 'https://fonts.example/style.css' },
      origin,
    ).action,
    'block',
  )
})

test('counts page and worker shell requests without hiding cache fetches', () => {
  assert.deepEqual(
    summarizeStaticShellRequests([
      { label: 'GET /', targetType: 'page' },
      { label: 'GET /assets/app.js', targetType: 'page' },
      { label: 'GET /', targetType: 'service_worker' },
      { label: 'GET /manifest.webmanifest', targetType: 'service_worker' },
    ]),
    {
      rootGetCount: 2,
      pageRootGetCount: 1,
      staticGetCount: 4,
      targetTypeCounts: {
        page: 2,
        service_worker: 2,
      },
      rootGetCountByTargetType: {
        page: 1,
        service_worker: 1,
      },
      requestCountsByTargetType: {
        page: {
          'GET /': 1,
          'GET /assets/app.js': 1,
        },
        service_worker: {
          'GET /': 1,
          'GET /manifest.webmanifest': 1,
        },
      },
    },
  )
  assert.throws(
    () => summarizeStaticShellRequests([
      { label: 'GET /assets/app.js', targetType: 'page' },
      { label: 'GET /', targetType: 'service_worker' },
    ]),
    /exactly one unauthenticated page root navigation GET/,
  )
  assert.throws(
    () => summarizeStaticShellRequests([
      { label: 'GET /', targetType: 'page' },
      { label: 'GET /', targetType: 'page' },
    ]),
    /exactly one unauthenticated page root navigation GET/,
  )
})

test('asserts the canonical service-worker cache matrix exactly once', () => {
  assert.deepEqual(
    assertServiceWorkerCacheMatrix(
      {
        'GET /': 1,
        'GET /manifest.webmanifest': 1,
        'GET /sw.js': 1,
      },
      ['/', '/manifest.webmanifest'],
    ),
    {
      'GET /': 1,
      'GET /manifest.webmanifest': 1,
    },
  )
  assert.throws(
    () => assertServiceWorkerCacheMatrix(
      {
        'GET /': 2,
        'GET /manifest.webmanifest': 1,
      },
      ['/', '/manifest.webmanifest'],
    ),
    /must each be observed exactly once/,
  )
  assert.throws(
    () => assertServiceWorkerCacheMatrix(
      { 'GET /': 1 },
      ['/', '/manifest.webmanifest'],
    ),
    /do not match APP_SHELL/,
  )
})

class FakeCdp {
  constructor(pageTargetId) {
    this.pageTargetId = pageTargetId
    this.listeners = new Map()
    this.commands = []
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || []
    listeners.push(listener)
    this.listeners.set(method, listeners)
  }

  off(method, listener) {
    const listeners = this.listeners.get(method) || []
    this.listeners.set(method, listeners.filter(candidate => candidate !== listener))
  }

  emit(method, params, sessionId = null) {
    for (const listener of this.listeners.get(method) || []) {
      listener(params, sessionId)
    }
  }

  async send(method, params = {}, sessionId = null) {
    this.commands.push({ method, params, sessionId })
    if (method === 'Target.autoAttachRelated') {
      queueMicrotask(() => {
        this.emit('Target.attachedToTarget', {
          sessionId: 'page-session',
          targetInfo: {
            targetId: this.pageTargetId,
            type: 'page',
          },
          waitingForDebugger: false,
        })
      })
    }
    return {}
  }
}

test('recursively intercepts service workers, nested workers, and every request', async () => {
  const cdp = new FakeCdp('page-target')
  const interceptor = new RemoteEntryInterceptor({
    cdp,
    pageTargetId: 'page-target',
    targetOrigin: 'https://device.example.ts.net',
    quietMs: 0,
  })
  await interceptor.start()

  cdp.emit('Target.attachedToTarget', {
    sessionId: 'duplicate-page-session',
    targetInfo: {
      targetId: 'page-target',
      type: 'page',
    },
    waitingForDebugger: false,
  })
  cdp.emit('Target.attachedToTarget', {
    sessionId: 'service-session',
    targetInfo: {
      targetId: 'service-target',
      type: 'service_worker',
      url: 'https://device.example.ts.net/sw.js',
    },
    waitingForDebugger: true,
  })
  cdp.emit('Target.attachedToTarget', {
    sessionId: 'duplicate-service-session',
    targetInfo: {
      targetId: 'service-target',
      type: 'service_worker',
      url: 'https://device.example.ts.net/sw.js',
    },
    waitingForDebugger: true,
  }, 'page-session')
  cdp.emit('Target.attachedToTarget', {
    sessionId: 'nested-session',
    targetInfo: {
      targetId: 'nested-target',
      type: 'worker',
    },
    waitingForDebugger: true,
  }, 'service-session')
  await interceptor.waitForSettled()

  cdp.emit('Fetch.requestPaused', {
    requestId: 'page-root',
    networkId: 'network-shared-root',
    request: {
      method: 'GET',
      url: 'https://device.example.ts.net/',
    },
  }, 'page-session')
  cdp.emit('Fetch.requestPaused', {
    requestId: 'worker-root',
    networkId: 'network-shared-root',
    request: {
      method: 'GET',
      url: 'https://device.example.ts.net/',
    },
  }, 'service-session')
  cdp.emit('Fetch.requestPaused', {
    requestId: 'duplicate-worker-root',
    networkId: 'network-shared-root',
    request: {
      method: 'GET',
      url: 'https://device.example.ts.net/',
    },
  }, 'duplicate-service-session')
  cdp.emit('Fetch.requestPaused', {
    requestId: 'worker-manifest',
    networkId: 'network-worker-manifest',
    request: {
      method: 'GET',
      url: 'https://device.example.ts.net/manifest.webmanifest',
    },
  }, 'service-session')
  cdp.emit('Fetch.requestPaused', {
    requestId: 'nested-api',
    networkId: 'network-nested-api',
    request: {
      method: 'GET',
      url: 'https://device.example.ts.net/api/projects',
    },
  }, 'nested-session')
  cdp.emit('Fetch.requestPaused', {
    requestId: 'page-bootstrap',
    networkId: 'network-page-bootstrap',
    request: {
      method: 'GET',
      url: 'https://device.example.ts.net/api/config',
    },
  }, 'page-session')
  cdp.emit('Network.webSocketCreated', {
    requestId: 'vite-hmr',
    url: 'wss://device.example.ts.net/',
  }, 'page-session')
  cdp.emit('Network.loadingFailed', {
    requestId: 'vite-hmr',
    type: 'WebSocket',
    blockedReason: 'inspector',
  }, 'page-session')
  cdp.emit('Network.webSocketCreated', {
    requestId: 'nested-hmr',
    url: 'wss://device.example.ts.net/nested',
  }, 'nested-session')
  cdp.emit('Network.loadingFailed', {
    requestId: 'nested-hmr',
    type: 'WebSocket',
    blockedReason: 'inspector',
  }, 'nested-session')
  await interceptor.waitForSettled()

  const snapshot = interceptor.snapshot()
  assert.deepEqual(snapshot.targetTypes, ['page', 'service_worker', 'worker'])
  assert.deepEqual(
    summarizeStaticShellRequests(snapshot.forwarded),
    {
      rootGetCount: 2,
      pageRootGetCount: 1,
      staticGetCount: 3,
      targetTypeCounts: {
        page: 1,
        service_worker: 2,
      },
      rootGetCountByTargetType: {
        page: 1,
        service_worker: 1,
      },
      requestCountsByTargetType: {
        page: {
          'GET /': 1,
        },
        service_worker: {
          'GET /': 1,
          'GET /manifest.webmanifest': 1,
        },
      },
    },
  )
  assert.deepEqual(snapshot.blocked, [{
    label: 'GET /api/projects',
    targetType: 'worker',
  }])
  assert.deepEqual(snapshot.fulfilled, [{
    label: 'GET /api/config',
    targetType: 'page',
  }])
  assert.deepEqual(snapshot.webSocket, {
    attemptedCount: 2,
    targetTypeCounts: {
      page: 1,
      worker: 1,
    },
    handshakeRequestCount: 0,
    handshakeResponseCount: 0,
    framesSent: 0,
    framesReceived: 0,
    blockedCount: 2,
    failureCount: 2,
    errorCount: 0,
    closedCount: 0,
  })
  for (const sessionId of ['page-session', 'nested-session']) {
    assert(cdp.commands.some(command => (
      command.method === 'Target.setAutoAttach'
      && command.params.autoAttach === true
      && command.sessionId === sessionId
    )))
    assert(cdp.commands.some(command => (
      command.method === 'Fetch.enable'
      && command.sessionId === sessionId
    )))
    assert(cdp.commands.some(command => (
      command.method === 'Network.enable'
      && command.sessionId === sessionId
    )))
    assert(cdp.commands.some(command => (
      command.method === 'Network.setBlockedURLs'
      && command.params.urlPatterns.length === 2
      && command.sessionId === sessionId
    )))
  }
  for (const sessionId of ['duplicate-page-session', 'duplicate-service-session']) {
    assert(!cdp.commands.some(command => (
      command.method === 'Fetch.enable'
      && command.sessionId === sessionId
    )))
  }
  assert(cdp.commands.some(command => (
    command.method === 'Fetch.enable'
    && command.sessionId === 'service-session'
  )))
  assert(!cdp.commands.some(command => (
    ['Network.enable', 'Network.setBlockedURLs'].includes(command.method)
    && command.sessionId === 'service-session'
  )))
  assert(cdp.commands.some(command => (
    command.method === 'Runtime.runIfWaitingForDebugger'
    && command.sessionId === 'duplicate-service-session'
  )))
  const serviceCommands = cdp.commands.filter(
    command => command.sessionId === 'service-session',
  )
  assert(
    serviceCommands.findIndex(command => command.method === 'Fetch.enable')
    < serviceCommands.findIndex(
      command => command.method === 'Runtime.runIfWaitingForDebugger',
    ),
  )
  assert.deepEqual(
    cdp.commands
      .filter(command => [
        'Fetch.continueRequest',
        'Fetch.failRequest',
        'Fetch.fulfillRequest',
      ].includes(command.method))
      .map(command => command.params.requestId)
      .sort(),
    [
      'duplicate-worker-root',
      'nested-api',
      'page-bootstrap',
      'page-root',
      'worker-manifest',
      'worker-root',
    ],
  )
  await interceptor.stop()
})

test('keeps delayed requests blocked after the first quiet window', async () => {
  const cdp = new FakeCdp('page-target')
  const interceptor = new RemoteEntryInterceptor({
    cdp,
    pageTargetId: 'page-target',
    targetOrigin: 'https://device.example.ts.net',
    quietMs: 0,
  })
  await interceptor.start()
  await interceptor.waitForSettled()

  await new Promise(resolve => {
    setTimeout(() => {
      cdp.emit('Fetch.requestPaused', {
        requestId: 'delayed-api',
        networkId: 'network-delayed-api',
        request: {
          method: 'GET',
          url: 'https://device.example.ts.net/api/projects',
        },
      }, 'page-session')
      resolve()
    }, 5)
  })
  await interceptor.waitForSettled()

  assert.deepEqual(interceptor.snapshot().blocked, [{
    label: 'GET /api/projects',
    targetType: 'page',
  }])
  await interceptor.stop()
})

test('bounds unresolved target activity', async () => {
  const cdp = new FakeCdp('page-target')
  const interceptor = new RemoteEntryInterceptor({
    cdp,
    pageTargetId: 'page-target',
    targetOrigin: 'https://device.example.ts.net',
    quietMs: 0,
  })
  interceptor._track(new Promise(() => {}))
  await assert.rejects(
    interceptor.waitForSettled(10),
    /Remote entry target activity did not settle/,
  )
})

test('rejects a WebSocket handshake response', async () => {
  const cdp = new FakeCdp('page-target')
  const interceptor = new RemoteEntryInterceptor({
    cdp,
    pageTargetId: 'page-target',
    targetOrigin: 'https://device.example.ts.net',
    quietMs: 0,
  })
  await interceptor.start()
  cdp.emit('Network.webSocketCreated', {
    requestId: 'unblocked-hmr',
    url: 'wss://device.example.ts.net/',
  }, 'page-session')
  cdp.emit('Network.webSocketHandshakeResponseReceived', {
    requestId: 'unblocked-hmr',
    response: { status: 101 },
  }, 'page-session')

  await assert.rejects(
    interceptor.waitForSettled(),
    /opened a WebSocket handshake/,
  )
  await interceptor.stop().catch(() => null)
})

test('rejects an outbound WebSocket handshake request', async () => {
  const cdp = new FakeCdp('page-target')
  const interceptor = new RemoteEntryInterceptor({
    cdp,
    pageTargetId: 'page-target',
    targetOrigin: 'https://device.example.ts.net',
    quietMs: 0,
  })
  await interceptor.start()
  cdp.emit('Network.webSocketCreated', {
    requestId: 'unblocked-hmr',
    url: 'wss://device.example.ts.net/',
  }, 'page-session')
  cdp.emit('Network.webSocketWillSendHandshakeRequest', {
    requestId: 'unblocked-hmr',
    request: { headers: {} },
  }, 'page-session')

  await assert.rejects(
    interceptor.waitForSettled(),
    /sent a WebSocket handshake/,
  )
  await interceptor.stop().catch(() => null)
})

test('rejects a WebSocket frame', async () => {
  const cdp = new FakeCdp('page-target')
  const interceptor = new RemoteEntryInterceptor({
    cdp,
    pageTargetId: 'page-target',
    targetOrigin: 'https://device.example.ts.net',
    quietMs: 0,
  })
  await interceptor.start()
  cdp.emit('Network.webSocketCreated', {
    requestId: 'unblocked-hmr',
    url: 'wss://device.example.ts.net/',
  }, 'page-session')
  cdp.emit('Network.webSocketFrameSent', {
    requestId: 'unblocked-hmr',
    response: { opcode: 1, payloadData: 'connected' },
  }, 'page-session')

  await assert.rejects(
    interceptor.waitForSettled(),
    /sent a WebSocket frame/,
  )
  await interceptor.stop().catch(() => null)
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
