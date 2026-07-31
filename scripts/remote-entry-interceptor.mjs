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
    this.observedTargetTypes = new Set()
    this.configuredTargets = new Set()
    this.targetWaiters = new Map()
    this.pending = new Set()
    this.errors = []
    this.forwarded = []
    this.fulfilled = []
    this.blocked = []
    this.lastActivity = Date.now()
    this.started = false
    this.stopped = false
    this.attachedListener = event => {
      this._track(this._configureTarget(event))
    }
    this.detachedListener = event => {
      this.sessions.delete(event.sessionId)
    }
    this.requestListener = (event, sessionId) => {
      this._track(this._handleRequest(event, sessionId))
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

  async _configureTarget(event) {
    const { sessionId, targetInfo, waitingForDebugger } = event
    assert(sessionId, 'Auto-attached target has no flattened session')
    this.lastActivity = Date.now()
    this.sessions.set(sessionId, {
      targetId: targetInfo.targetId,
      targetType: targetInfo.type || 'unknown',
    })
    this.observedTargetTypes.add(targetInfo.type || 'unknown')
    await this.cdp.send('Target.setAutoAttach', {
      autoAttach: true,
      waitForDebuggerOnStart: true,
      flatten: true,
    }, sessionId)
    await this.cdp.send('Fetch.enable', {
      patterns: [{ urlPattern: '*', requestStage: 'Request' }],
    }, sessionId)
    this.configuredTargets.add(targetInfo.targetId)
    for (const resolve of this.targetWaiters.get(targetInfo.targetId) || []) resolve()
    this.targetWaiters.delete(targetInfo.targetId)
    if (waitingForDebugger) {
      await this.cdp.send('Runtime.runIfWaitingForDebugger', {}, sessionId)
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
    if (decision.action === 'fulfill') {
      this.fulfilled.push(entry)
      await this.cdp.send('Fetch.fulfillRequest', {
        requestId: event.requestId,
        responseCode: decision.response.status,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: Buffer.from(JSON.stringify(decision.response.body)).toString('base64'),
      }, sessionId)
      return
    }
    if (decision.action === 'block') {
      this.blocked.push(entry)
      await this.cdp.send('Fetch.failRequest', {
        requestId: event.requestId,
        errorReason: 'BlockedByClient',
      }, sessionId)
      return
    }
    this.forwarded.push(entry)
    await this.cdp.send('Fetch.continueRequest', {
      requestId: event.requestId,
    }, sessionId)
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
    await this.cdp.send('Target.autoAttachRelated', {
      targetId: this.pageTargetId,
      waitForDebuggerOnStart: true,
    })
    await this.waitForTarget(this.pageTargetId)
    await this.waitForSettled()
  }

  async waitForSettled(timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() <= deadline) {
      await Promise.all([...this.pending])
      if (this.errors.length > 0) throw this.errors[0]
      const remaining = this.quietMs - (Date.now() - this.lastActivity)
      if (remaining <= 0 && this.pending.size === 0) return
      await sleep(Math.max(1, Math.min(remaining, 50)))
    }
    throw new Error('Remote entry target activity did not settle')
  }

  snapshot() {
    assert.equal(this.errors.length, 0)
    return {
      forwarded: [...this.forwarded],
      fulfilled: [...this.fulfilled],
      blocked: [...this.blocked],
      targetTypes: [...this.observedTargetTypes].sort(),
    }
  }

  async stop() {
    if (!this.started || this.stopped) return
    this.stopped = true
    await this.waitForSettled()
    await Promise.allSettled([...this.sessions.keys()].flatMap(sessionId => [
      this.cdp.send('Fetch.disable', {}, sessionId),
      this.cdp.send('Target.setAutoAttach', {
        autoAttach: false,
        waitForDebuggerOnStart: false,
        flatten: true,
      }, sessionId),
    ]))
    await this.cdp.send('Target.setAutoAttach', {
      autoAttach: false,
      waitForDebuggerOnStart: false,
      flatten: true,
    })
    this.cdp.off('Target.attachedToTarget', this.attachedListener)
    this.cdp.off('Target.detachedFromTarget', this.detachedListener)
    this.cdp.off('Fetch.requestPaused', this.requestListener)
  }
}
