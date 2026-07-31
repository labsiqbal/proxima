import assert from 'node:assert/strict'
import { remoteRequestPolicy } from './accessibility-audit-policy.mjs'

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

export class RemoteEntryInterceptor {
  constructor({
    cdp,
    pageTargetId,
    targetOrigin,
    quietMs = 500,
  }) {
    this.cdp = cdp
    this.pageTargetId = pageTargetId
    this.targetOrigin = targetOrigin
    this.quietMs = quietMs
    this.sessions = new Map()
    this.targetOwners = new Map()
    this.targetReadiness = new Map()
    this.observedTargetTypes = new Set()
    this.configuredTargets = new Set()
    this.targetWaiters = new Map()
    this.seenNetworkRequests = new Set()
    this.webSockets = new Map()
    this.pending = new Set()
    this.errors = []
    this.forwarded = []
    this.fulfilled = []
    this.blocked = []
    this.lastActivity = Date.now()
    this.started = false
    this.stopped = false
    this.attachedListener = (event, parentSessionId) => {
      this._track(this._configureTarget(event, parentSessionId))
    }
    this.detachedListener = event => {
      const target = this.sessions.get(event.sessionId)
      if (target?.owned && this.targetOwners.get(target.targetId) === event.sessionId) {
        this.targetOwners.delete(target.targetId)
        this.targetReadiness.delete(target.targetId)
        this.configuredTargets.delete(target.targetId)
      }
      this.sessions.delete(event.sessionId)
    }
    this.requestListener = (event, sessionId) => {
      this._track(this._handleRequest(event, sessionId))
    }
    this.webSocketCreatedListener = (event, sessionId) => {
      this._track(this._recordWebSocket(event, sessionId, 'created'))
    }
    this.webSocketHandshakeRequestListener = (event, sessionId) => {
      this._track(this._recordWebSocket(event, sessionId, 'handshakeRequest'))
    }
    this.webSocketHandshakeResponseListener = (event, sessionId) => {
      this._track(this._recordWebSocket(event, sessionId, 'handshakeResponse'))
    }
    this.webSocketFrameSentListener = (event, sessionId) => {
      this._track(this._recordWebSocket(event, sessionId, 'frameSent'))
    }
    this.webSocketFrameReceivedListener = (event, sessionId) => {
      this._track(this._recordWebSocket(event, sessionId, 'frameReceived'))
    }
    this.webSocketErrorListener = (event, sessionId) => {
      this._track(this._recordWebSocket(event, sessionId, 'error'))
    }
    this.webSocketClosedListener = (event, sessionId) => {
      this._track(this._recordWebSocket(event, sessionId, 'closed'))
    }
    this.loadingFailedListener = (event, sessionId) => {
      if (event.type === 'WebSocket') {
        this._track(this._recordWebSocket(event, sessionId, 'loadingFailed'))
      }
    }
  }

  _track(task) {
    let tracked
    tracked = Promise.resolve(task)
      .catch(error => {
        this.errors.push(error instanceof Error ? error : new Error(String(error)))
      })
      .finally(() => this.pending.delete(tracked))
    this.pending.add(tracked)
  }

  _send(method, params = {}, sessionId = null, timeoutMs = 5000) {
    let timeout
    const deadline = new Promise((_, reject) => {
      timeout = setTimeout(() => {
        reject(new Error(`Timed out sending ${method}`))
      }, timeoutMs)
    })
    return Promise.race([
      this.cdp.send(method, params, sessionId),
      deadline,
    ]).finally(() => clearTimeout(timeout))
  }

  async _waitForOwnerPolicy(targetId, timeoutMs = 5000) {
    const ownerDeadline = Date.now() + timeoutMs
    while (!this.targetReadiness.has(targetId) && Date.now() < ownerDeadline) {
      await sleep(10)
    }
    const readiness = this.targetReadiness.get(targetId)
    assert(readiness, `Target ${targetId} has no root owner policy`)
    let timeout
    const deadline = new Promise((_, reject) => {
      timeout = setTimeout(() => {
        reject(new Error(`Timed out securing target ${targetId}`))
      }, Math.max(1, ownerDeadline - Date.now()))
    })
    return Promise.race([readiness, deadline]).finally(() => clearTimeout(timeout))
  }

  async _configureTarget(event, parentSessionId = null) {
    const { sessionId, targetInfo, waitingForDebugger } = event
    assert(sessionId, 'Auto-attached target has no flattened session')
    this.lastActivity = Date.now()
    const existingOwner = this.targetOwners.get(targetInfo.targetId)
    if (existingOwner && existingOwner !== sessionId) {
      this.sessions.set(sessionId, {
        targetId: targetInfo.targetId,
        targetType: targetInfo.type || 'unknown',
        owned: false,
        parentSessionId,
      })
      await this._waitForOwnerPolicy(targetInfo.targetId)
      if (waitingForDebugger) {
        await this._send('Runtime.runIfWaitingForDebugger', {}, sessionId)
      }
      return
    }
    this.targetOwners.set(targetInfo.targetId, sessionId)
    let resolveReady
    let rejectReady
    const ready = new Promise((resolve, reject) => {
      resolveReady = resolve
      rejectReady = reject
    })
    ready.catch(() => null)
    this.targetReadiness.set(targetInfo.targetId, ready)
    this.sessions.set(sessionId, {
      targetId: targetInfo.targetId,
      targetType: targetInfo.type || 'unknown',
      owned: true,
      parentSessionId,
    })
    this.observedTargetTypes.add(targetInfo.type || 'unknown')
    try {
      const serviceWorker = targetInfo.type === 'service_worker'
      if (serviceWorker) {
        const workerUrl = new URL(targetInfo.url)
        assert.equal(workerUrl.origin, this.targetOrigin)
        assert.equal(workerUrl.pathname, '/sw.js')
      }
      if (!serviceWorker) {
        await this._send('Network.enable', {}, sessionId)
        await this._send('Network.setBlockedURLs', {
          urlPatterns: [
          { urlPattern: 'ws://*', block: true },
          { urlPattern: 'wss://*', block: true },
          ],
        }, sessionId)
      }
      await this._send('Fetch.enable', {
        patterns: [{ urlPattern: '*', requestStage: 'Request' }],
      }, sessionId)
      await this._send('Target.setAutoAttach', {
        autoAttach: true,
        waitForDebuggerOnStart: true,
        flatten: true,
      }, sessionId)
      this.configuredTargets.add(targetInfo.targetId)
      for (const resolve of this.targetWaiters.get(targetInfo.targetId) || []) resolve()
      this.targetWaiters.delete(targetInfo.targetId)
      resolveReady()
      if (waitingForDebugger) {
        await this._send('Runtime.runIfWaitingForDebugger', {}, sessionId)
      }
    } catch (error) {
      rejectReady(error)
      throw new Error(
        `${targetInfo.type || 'unknown'} target ${targetInfo.targetId}`
        + `${parentSessionId ? ` via ${parentSessionId}` : ''}: ${error.message}`,
      )
    }
  }

  async _handleRequest(event, sessionId) {
    assert(sessionId, 'Intercepted request has no flattened target session')
    const target = this.sessions.get(sessionId)
    assert(target, `Intercepted request used unknown session ${sessionId}`)
    this.lastActivity = Date.now()
    const decision = remoteRequestPolicy(event.request, this.targetOrigin)
    const entry = {
      label: decision.label,
      targetType: target.targetType,
    }
    const networkKey = `${target.targetId}:${event.networkId || event.requestId}`
    const shouldRecord = !this.seenNetworkRequests.has(networkKey)
    this.seenNetworkRequests.add(networkKey)
    if (decision.action === 'fulfill') {
      if (shouldRecord) this.fulfilled.push(entry)
      const responseBody = decision.response.raw
        ? decision.response.body
        : JSON.stringify(decision.response.body)
      await this._send('Fetch.fulfillRequest', {
        requestId: event.requestId,
        responseCode: decision.response.status,
        responseHeaders: [{
          name: 'Content-Type',
          value: decision.response.contentType || 'application/json',
        }],
        body: Buffer.from(responseBody).toString('base64'),
      }, sessionId)
      return
    }
    if (decision.action === 'block') {
      if (shouldRecord) this.blocked.push(entry)
      await this._send('Fetch.failRequest', {
        requestId: event.requestId,
        errorReason: 'BlockedByClient',
      }, sessionId)
      return
    }
    if (shouldRecord) this.forwarded.push(entry)
    await this._send('Fetch.continueRequest', {
      requestId: event.requestId,
    }, sessionId)
  }

  async _recordWebSocket(event, sessionId, kind) {
    assert(sessionId, 'WebSocket event has no flattened target session')
    const target = this.sessions.get(sessionId)
    assert(target, `WebSocket event used unknown session ${sessionId}`)
    if (!target.owned || this.targetOwners.get(target.targetId) !== sessionId) return
    this.lastActivity = Date.now()
    const key = `${target.targetId}:${event.requestId}`
    const record = this.webSockets.get(key) || {
      targetType: target.targetType,
      created: false,
      handshakeRequest: false,
      handshakeResponse: false,
      framesSent: 0,
      framesReceived: 0,
      errors: 0,
      closed: false,
      loadingFailed: false,
      blocked: false,
    }
    if (kind === 'created') record.created = true
    if (kind === 'handshakeRequest') record.handshakeRequest = true
    if (kind === 'handshakeResponse') record.handshakeResponse = true
    if (kind === 'frameSent') record.framesSent += 1
    if (kind === 'frameReceived') record.framesReceived += 1
    if (kind === 'error') record.errors += 1
    if (kind === 'closed') record.closed = true
    if (kind === 'loadingFailed') {
      record.loadingFailed = true
      record.blocked = event.blockedReason === 'inspector'
    }
    this.webSockets.set(key, record)
    assert.equal(record.handshakeRequest, false, 'Remote entry sent a WebSocket handshake')
    assert.equal(record.handshakeResponse, false, 'Remote entry opened a WebSocket handshake')
    assert.equal(record.framesSent, 0, 'Remote entry sent a WebSocket frame')
    assert.equal(record.framesReceived, 0, 'Remote entry received a WebSocket frame')
  }

  waitForTarget(targetId, timeoutMs = 5000) {
    if (this.configuredTargets.has(targetId)) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error(`Timed out attaching to target ${targetId}`))
      }, timeoutMs)
      const ready = () => {
        clearTimeout(timeout)
        resolve()
      }
      const waiters = this.targetWaiters.get(targetId) || []
      waiters.push(ready)
      this.targetWaiters.set(targetId, waiters)
    })
  }

  async start() {
    assert(!this.started, 'Remote entry interception already started')
    this.started = true
    this.cdp.on('Target.attachedToTarget', this.attachedListener)
    this.cdp.on('Target.detachedFromTarget', this.detachedListener)
    this.cdp.on('Fetch.requestPaused', this.requestListener)
    this.cdp.on('Network.webSocketCreated', this.webSocketCreatedListener)
    this.cdp.on(
      'Network.webSocketWillSendHandshakeRequest',
      this.webSocketHandshakeRequestListener,
    )
    this.cdp.on(
      'Network.webSocketHandshakeResponseReceived',
      this.webSocketHandshakeResponseListener,
    )
    this.cdp.on('Network.webSocketFrameSent', this.webSocketFrameSentListener)
    this.cdp.on('Network.webSocketFrameReceived', this.webSocketFrameReceivedListener)
    this.cdp.on('Network.webSocketFrameError', this.webSocketErrorListener)
    this.cdp.on('Network.webSocketClosed', this.webSocketClosedListener)
    this.cdp.on('Network.loadingFailed', this.loadingFailedListener)
    await this._send('Target.autoAttachRelated', {
      targetId: this.pageTargetId,
      waitForDebuggerOnStart: true,
    })
    await this.waitForTarget(this.pageTargetId)
    await this.waitForSettled()
  }

  async waitForSettled(timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() <= deadline) {
      const pending = [...this.pending]
      if (pending.length > 0) {
        let timeout
        try {
          await Promise.race([
            Promise.all(pending),
            new Promise((_, reject) => {
              timeout = setTimeout(() => {
                reject(new Error('Remote entry target activity did not settle'))
              }, Math.max(1, deadline - Date.now()))
            }),
          ])
        } finally {
          clearTimeout(timeout)
        }
      }
      if (this.errors.length > 0) throw this.errors[0]
      const remaining = this.quietMs - (Date.now() - this.lastActivity)
      if (remaining <= 0 && this.pending.size === 0) return
      await sleep(Math.max(1, Math.min(remaining, 50)))
    }
    throw new Error('Remote entry target activity did not settle')
  }

  snapshot() {
    const webSockets = [...this.webSockets.values()]
    const targetTypeCounts = {}
    for (const socket of webSockets) {
      targetTypeCounts[socket.targetType] = (targetTypeCounts[socket.targetType] || 0) + 1
    }
    return {
      forwarded: [...this.forwarded],
      fulfilled: [...this.fulfilled],
      blocked: [...this.blocked],
      targetTypes: [...this.observedTargetTypes].sort(),
      webSocket: {
        attemptedCount: webSockets.length,
        targetTypeCounts,
        handshakeRequestCount: webSockets.filter(socket => socket.handshakeRequest).length,
        handshakeResponseCount: webSockets.filter(socket => socket.handshakeResponse).length,
        framesSent: webSockets.reduce((total, socket) => total + socket.framesSent, 0),
        framesReceived: webSockets.reduce(
          (total, socket) => total + socket.framesReceived,
          0,
        ),
        blockedCount: webSockets.filter(socket => socket.blocked).length,
        failureCount: webSockets.filter(socket => socket.loadingFailed).length,
        errorCount: webSockets.reduce((total, socket) => total + socket.errors, 0),
        closedCount: webSockets.filter(socket => socket.closed).length,
      },
    }
  }

  inspectedTargetIds() {
    return [...this.targetOwners.keys()]
  }

  async stop() {
    if (!this.started || this.stopped) return
    this.stopped = true
    let settleError = null
    try {
      await this.waitForSettled()
    } catch (error) {
      settleError = error
    }
    const ownerSessions = [...this.sessions.entries()]
      .filter(([, target]) => target.owned)
      .map(([sessionId]) => sessionId)
    await Promise.allSettled(ownerSessions.flatMap(sessionId => [
      this._send('Fetch.disable', {}, sessionId),
      this._send('Network.setBlockedURLs', { urlPatterns: [] }, sessionId),
      this._send('Network.disable', {}, sessionId),
      this._send('Target.setAutoAttach', {
        autoAttach: false,
        waitForDebuggerOnStart: false,
        flatten: true,
      }, sessionId),
    ]))
    await this._send('Target.setAutoAttach', {
      autoAttach: false,
      waitForDebuggerOnStart: false,
      flatten: true,
    }).catch(() => null)
    this.cdp.off('Target.attachedToTarget', this.attachedListener)
    this.cdp.off('Target.detachedFromTarget', this.detachedListener)
    this.cdp.off('Fetch.requestPaused', this.requestListener)
    this.cdp.off('Network.webSocketCreated', this.webSocketCreatedListener)
    this.cdp.off(
      'Network.webSocketWillSendHandshakeRequest',
      this.webSocketHandshakeRequestListener,
    )
    this.cdp.off(
      'Network.webSocketHandshakeResponseReceived',
      this.webSocketHandshakeResponseListener,
    )
    this.cdp.off('Network.webSocketFrameSent', this.webSocketFrameSentListener)
    this.cdp.off('Network.webSocketFrameReceived', this.webSocketFrameReceivedListener)
    this.cdp.off('Network.webSocketFrameError', this.webSocketErrorListener)
    this.cdp.off('Network.webSocketClosed', this.webSocketClosedListener)
    this.cdp.off('Network.loadingFailed', this.loadingFailedListener)
    if (settleError) throw settleError
  }
}
