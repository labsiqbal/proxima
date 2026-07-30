#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import { createRequire } from 'node:module'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const webRoot = path.join(repoRoot, 'apps', 'web')
const apiRoot = path.join(repoRoot, 'apps', 'api')
const venvPython = path.join(apiRoot, '.venv', 'bin', 'python')
const serve = path.join(repoRoot, 'apps', 'api', 'scripts', 'serve.py')
const webDist = path.join(webRoot, 'dist')
const lighthouse = path.join(webRoot, 'node_modules', '.bin', 'lighthouse')
const chromePath = process.env.CHROME_PATH || '/usr/bin/google-chrome'
const require = createRequire(path.join(webRoot, 'package.json'))
const WebSocket = require('ws')
const evidenceDir = path.resolve(
  process.env.PROXIMA_A11Y_EVIDENCE_DIR
    || path.join(repoRoot, 'docs', 'evidence', 'auth-onboarding-accessibility'),
)

function canonicalThemes() {
  const source = fs.readFileSync(path.join(webRoot, 'src', 'theme.ts'), 'utf8')
  const catalog = source.match(/export const THEMES:[\s\S]*?=\s*\[([\s\S]*?)\n\]/)
  const type = source.match(/export type ThemeKey\s*=\s*([^\n]+)/)
  assert(catalog && type, 'Could not read the canonical theme catalog')
  const catalogKeys = [...catalog[1].matchAll(/\bkey:\s*'([^']+)'/g)].map(match => match[1])
  const typeKeys = [...type[1].matchAll(/'([^']+)'/g)].map(match => match[1])
  assert(catalogKeys.length > 0, 'Canonical theme catalog is empty')
  assert.deepEqual(catalogKeys, typeKeys, 'ThemeKey and THEMES must stay in exact parity')
  return catalogKeys
}

const themes = canonicalThemes()

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

function allowlistedEnvironment(overrides = {}) {
  const environment = {}
  for (const name of ['PATH', 'LANG', 'LC_ALL', 'TZ']) {
    if (process.env[name]) environment[name] = process.env[name]
  }
  return { ...environment, ...overrides }
}

function disposableApiEnvironment({ fixtureRoot, fixtureHome, dataRoot, workspaceRoot, apiPort }) {
  const environment = allowlistedEnvironment({
    HOME: fixtureHome,
    TMPDIR: path.join(fixtureRoot, 'tmp'),
    XDG_CACHE_HOME: path.join(fixtureRoot, 'cache'),
    XDG_CONFIG_HOME: path.join(fixtureRoot, 'config'),
    XDG_DATA_HOME: dataRoot,
    UV_CACHE_DIR: path.join(fixtureRoot, 'uv-cache'),
    PROXIMA_REPO_ROOT: repoRoot,
    PROXIMA_DB_PATH: path.join(dataRoot, 'proxima.db'),
    PROXIMA_WORKSPACE_ROOT: workspaceRoot,
    PROXIMA_HERMES_PROFILES_ROOT: path.join(dataRoot, 'hermes-profiles'),
    PROXIMA_SOURCE_HERMES_HOME: path.join(dataRoot, 'source-hermes'),
    PROXIMA_HERMES_BIN: '/usr/bin/false',
    PROXIMA_PROJECTCTL_COMMAND: '/usr/bin/true',
    PROXIMA_WEB_DIST: webDist,
    PROXIMA_HOST: '127.0.0.1',
    PROXIMA_PORT: String(apiPort),
    PROXIMA_SINGLE_USER: '1',
    PROXIMA_SINGLE_USER_NAME: 'accessibility-owner',
    PROXIMA_LINK_ROOTS: fixtureHome,
    PROXIMA_START_WORKER: '0',
    PROXIMA_REFRESH_CREDENTIALS: '0',
    PROXIMA_UPDATE_CHECK: '0',
    PROXIMA_MANAGE_OS_ACL: '0',
    PROXIMA_CLAUDE_LIVE_HOME: '0',
    PROXIMA_PREVIEW_BIND: 'off',
    PROXIMA_GRAPH_SEMANTIC_EGRESS: '0',
    PROXIMA_FEATURE_MASTER_ORCHESTRATOR: '0',
    PROXIMA_FEATURE_SAFE_SELF_UPDATE: '0',
    PROXIMA_CANDIDATE_MODE: '0',
  })
  for (const name of [
    'HOME',
    'TMPDIR',
    'XDG_CACHE_HOME',
    'XDG_CONFIG_HOME',
    'XDG_DATA_HOME',
    'UV_CACHE_DIR',
    'PROXIMA_DB_PATH',
    'PROXIMA_WORKSPACE_ROOT',
    'PROXIMA_HERMES_PROFILES_ROOT',
    'PROXIMA_SOURCE_HERMES_HOME',
    'PROXIMA_LINK_ROOTS',
  ]) {
    assert(
      path.resolve(environment[name]).startsWith(`${path.resolve(fixtureRoot)}${path.sep}`)
        || path.resolve(environment[name]) === path.resolve(fixtureRoot),
      `${name} escaped the disposable fixture`,
    )
  }
  return environment
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      server.close(() => {
        if (typeof address === 'object' && address?.port) resolve(address.port)
        else reject(new Error('Could not allocate a local port'))
      })
    })
  })
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, response => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { body += chunk })
      response.on('end', () => {
        try {
          resolve(JSON.parse(body))
        } catch (error) {
          reject(error)
        }
      })
    })
    request.on('error', reject)
  })
}

async function waitForJson(url, predicate, label) {
  let lastError
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const value = await fetchJson(url)
      if (predicate(value)) return value
    } catch (error) {
      lastError = error
    }
    await sleep(100)
  }
  throw new Error(`${label} did not become ready${lastError ? `: ${lastError}` : ''}`)
}

class CdpClient {
  constructor(socket) {
    this.socket = socket
    this.nextId = 0
    this.pending = new Map()
    this.listeners = new Map()
    socket.onmessage = event => {
      const message = JSON.parse(event.data)
      if (message.id && this.pending.has(message.id)) {
        const pending = this.pending.get(message.id)
        this.pending.delete(message.id)
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)))
        else pending.resolve(message.result)
        return
      }
      for (const listener of this.listeners.get(message.method) || []) {
        listener(message.params)
      }
    }
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.nextId
      this.pending.set(id, { resolve, reject })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
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
}

async function connectCdp(port, expectedUrl) {
  const pages = await waitForJson(
    `http://127.0.0.1:${port}/json`,
    value => Array.isArray(value) && value.some(page => page.type === 'page'),
    'Chrome DevTools',
  )
  const page = pages.find(candidate => candidate.url === expectedUrl)
    || pages.find(candidate => candidate.type === 'page')
  assert(page?.webSocketDebuggerUrl, 'Chrome page target has no debugger URL')
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  return new CdpClient(socket)
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text)
  }
  return result.result.value
}

async function waitForPage(cdp, expression, label) {
  let lastValue
  for (let attempt = 0; attempt < 120; attempt += 1) {
    lastValue = await evaluate(cdp, expression).catch(() => undefined)
    if (lastValue) return lastValue
    await sleep(100)
  }
  throw new Error(`${label} did not appear; last value: ${JSON.stringify(lastValue)}`)
}

async function navigate(cdp, url, heading) {
  await cdp.send('Page.navigate', { url })
  const expectedLocation = new URL(url).toString()
  await waitForPage(
    cdp,
    `location.href === ${JSON.stringify(expectedLocation)}
      && document.querySelector('h1')?.textContent === ${JSON.stringify(heading)}`,
    heading,
  )
}

async function setInput(cdp, name, value) {
  const changed = await evaluate(cdp, `(() => {
    const input = document.querySelector('input[name=${JSON.stringify(name)}]')
    if (!(input instanceof HTMLInputElement)) return false
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    setter.call(input, ${JSON.stringify(value)})
    input.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  })()`)
  assert(changed, `Missing input ${name}`)
}

async function clickButton(cdp, label) {
  const clicked = await evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('button')]
      .find(candidate => candidate.textContent.trim() === ${JSON.stringify(label)})
    if (!button) return false
    button.click()
    return true
  })()`)
  assert(clicked, `Missing button ${label}`)
}

async function focusButton(cdp, label) {
  const focused = await evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('button')]
      .find(candidate => candidate.textContent.trim() === ${JSON.stringify(label)})
    if (!button) return false
    button.focus()
    return document.activeElement === button
  })()`)
  assert(focused, `Could not focus ${label}`)
}

async function pressKey(cdp, key, code, keyCode, text = '') {
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key,
    code,
    windowsVirtualKeyCode: keyCode,
    nativeVirtualKeyCode: keyCode,
    text,
  })
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key,
    code,
    windowsVirtualKeyCode: keyCode,
    nativeVirtualKeyCode: keyCode,
  })
}

async function startAnnouncementTrace(cdp) {
  return evaluate(cdp, `(() => {
    window.__proximaA11yEvents = []
    window.__proximaA11yObserver?.disconnect()
    document.addEventListener('focusin', window.__proximaA11yFocusListener ||= event => {
      const target = event.target
      window.__proximaA11yEvents.push({
        type: 'focus',
        name: target?.getAttribute?.('name') || target?.textContent?.trim() || '',
      })
    })
    window.__proximaA11yObserver = new MutationObserver(records => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (!(node instanceof Element)) continue
          const alerts = node.matches('[role=alert]')
            ? [node]
            : [...node.querySelectorAll('[role=alert]')]
          for (const alert of alerts) {
            window.__proximaA11yEvents.push({
              type: 'alert',
              name: alert.textContent.trim(),
            })
          }
        }
      }
    })
    window.__proximaA11yObserver.observe(document.body, { childList: true, subtree: true })
    return document.activeElement?.getAttribute?.('name') || ''
  })()`)
}

async function announcementTrace(cdp) {
  return evaluate(cdp, 'window.__proximaA11yEvents || []')
}

function assertSingleAnnouncement(trace, fieldName, messagePattern, alreadyFocused = false) {
  const relevant = trace.filter(event => event.type === 'alert' || (
    event.type === 'focus' && event.name === fieldName
  ))
  assert.equal(relevant.filter(event => event.type === 'alert').length, 1)
  if (relevant[0]?.type === 'alert') {
    assert(alreadyFocused, `${fieldName} was not focused before its alert`)
    assert.match(relevant[0]?.name || '', messagePattern)
    return
  }
  assert.equal(relevant[0]?.type, 'focus')
  assert.equal(relevant[0]?.name, fieldName)
  assert.equal(relevant[1]?.type, 'alert')
  assert.match(relevant[1]?.name || '', messagePattern)
}

function axProperty(node, name) {
  return node.properties?.find(property => property.name === name)?.value?.value
}

async function accessibilitySummary(cdp) {
  const tree = await cdp.send('Accessibility.getFullAXTree')
  const byId = new Map(tree.nodes.map(node => [node.nodeId, node]))
  const visible = tree.nodes.filter(node => !node.ignored)
  const summarize = node => ({
    role: node.role?.value || '',
    name: node.name?.value || '',
    description: node.description?.value || '',
    focused: axProperty(node, 'focused') === true,
    invalid: axProperty(node, 'invalid') || false,
    pressed: axProperty(node, 'pressed'),
  })
  const descendantText = node => {
    const names = []
    const visit = childId => {
      const child = byId.get(childId)
      if (!child) return
      if (!child.ignored && ['StaticText', 'InlineTextBox'].includes(child.role?.value)) {
        const name = child.name?.value?.trim()
        if (name && names.at(-1) !== name) names.push(name)
      }
      for (const grandchildId of child.childIds || []) visit(grandchildId)
    }
    for (const childId of node.childIds || []) visit(childId)
    return names.join(' ')
  }
  const nodes = visible.map(summarize)
  return {
    mains: nodes.filter(node => node.role === 'main'),
    alerts: visible
      .filter(node => node.role?.value === 'alert')
      .map(node => ({ ...summarize(node), text: descendantText(node) })),
    focused: nodes.filter(node => node.focused),
    buttons: nodes.filter(node => node.role === 'button'),
    tabs: nodes.filter(node => node.role === 'tab'),
  }
}

async function screenshot(cdp, filename) {
  const capture = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  })
  fs.writeFileSync(path.join(evidenceDir, filename), Buffer.from(capture.data, 'base64'))
}

async function auditTheme(cdp, theme) {
  await evaluate(
    cdp,
    `localStorage.setItem('proxima.theme', ${JSON.stringify(theme)}); location.reload(); true`,
  )
  await waitForPage(
    cdp,
    `document.querySelector('h1')?.textContent === 'Welcome back'`,
    `${theme} login gate`,
  )
  await clickButton(cdp, 'Log in')
  await waitForPage(cdp, `document.querySelector('[role=alert]')?.textContent.includes('Enter your password')`, `${theme} error`)
  const result = await evaluate(cdp, `(() => {
    function channel(value) {
      const normalized = value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
      return normalized
    }
    function parseColor(value) {
      const rgb = value.match(/^rgba?\\(([^)]+)\\)$/)
      if (rgb) {
        const parts = rgb[1].split(/[ ,/]+/).filter(Boolean).slice(0, 3).map(Number)
        return parts.map(part => part / 255)
      }
      const srgb = value.match(/^color\\(srgb\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s/)]+)/)
      if (srgb) return srgb.slice(1, 4).map(Number)
      throw new Error('Unsupported computed color: ' + value)
    }
    function luminance(value) {
      const [red, green, blue] = parseColor(value).map(channel)
      return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
    }
    function contrast(foreground, background) {
      const a = luminance(foreground)
      const b = luminance(background)
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
    }
    const card = document.querySelector('.auth-card')
    const subtitle = document.querySelector('.auth-sub')
    const error = document.querySelector('.auth-error')
    const button = document.querySelector('.auth-submit')
    const password = document.querySelector('input[name=password]')
    password.focus()
    const cardStyle = getComputedStyle(card)
    const subtitleStyle = getComputedStyle(subtitle)
    const errorStyle = getComputedStyle(error)
    const buttonStyle = getComputedStyle(button)
    const focusStyle = getComputedStyle(password)
    return {
      theme: document.documentElement.dataset.theme,
      subtitleContrast: contrast(subtitleStyle.color, cardStyle.backgroundColor),
      errorContrast: contrast(errorStyle.color, cardStyle.backgroundColor),
      buttonContrast: contrast(buttonStyle.color, buttonStyle.backgroundColor),
      focus: {
        style: focusStyle.outlineStyle,
        width: parseFloat(focusStyle.outlineWidth),
        color: focusStyle.outlineColor,
      },
    }
  })()`)
  assert.equal(result.theme, theme)
  assert(result.subtitleContrast >= 4.5, `${theme} subtitle contrast is ${result.subtitleContrast}`)
  assert(result.errorContrast >= 4.5, `${theme} error contrast is ${result.errorContrast}`)
  assert(result.buttonContrast >= 4.5, `${theme} button contrast is ${result.buttonContrast}`)
  assert.notEqual(result.focus.style, 'none', `${theme} password focus is invisible`)
  assert(result.focus.width >= 2, `${theme} password focus width is ${result.focus.width}`)
  return result
}

function privateEntryUrl(value) {
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

function discoverPrivateTailscaleEntry(environment) {
  const configured = process.env.PROXIMA_A11Y_REMOTE_BASE?.trim()
  const configuredAddress = process.env.PROXIMA_A11Y_REMOTE_ADDRESS?.trim()
  if (configured) {
    assert(!configuredAddress || net.isIP(configuredAddress), 'PROXIMA_A11Y_REMOTE_ADDRESS must be an IP address')
    return { url: privateEntryUrl(configured), address: configuredAddress || null }
  }
  const proxyPort = process.env.PROXIMA_A11Y_REMOTE_PROXY_PORT?.trim() || '8765'
  assert(/^\d+$/.test(proxyPort), 'PROXIMA_A11Y_REMOTE_PROXY_PORT must be a port number')
  const command = process.env.PROXIMA_A11Y_TAILSCALE_BIN?.trim() || 'tailscale'
  const result = spawnSync(command, ['serve', 'status', '--json'], {
    env: environment,
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
  })
  assert.equal(result.status, 0, 'Could not read the current Tailscale Serve configuration')
  const status = JSON.parse(result.stdout)
  const candidates = Object.entries(status.Web || {}).filter(([, config]) => {
    const proxy = config?.Handlers?.['/']?.Proxy
    if (typeof proxy !== 'string') return false
    try {
      const target = new URL(proxy)
      return ['127.0.0.1', 'localhost', '::1'].includes(target.hostname)
        && (target.port || (target.protocol === 'https:' ? '443' : '80')) === proxyPort
    } catch {
      return false
    }
  })
  assert.equal(candidates.length, 1, 'Expected one Tailscale root entry for the current Proxima service')
  const origin = candidates[0][0]
  const servedPort = origin.match(/:(\d+)$/)?.[1] || '443'
  const protocol = status.TCP?.[servedPort]?.HTTPS ? 'https' : 'http'
  const device = spawnSync(command, ['status', '--json'], {
    env: environment,
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
  })
  assert.equal(device.status, 0, 'Could not read the current Tailscale device address')
  const deviceStatus = JSON.parse(device.stdout)
  const hostname = String(deviceStatus.Self?.DNSName || '').replace(/\.$/, '')
  assert(hostname, 'Current Tailscale device has no DNS name')
  const address = deviceStatus.Self?.TailscaleIPs?.find(candidate => net.isIP(candidate) === 4)
  assert(address, 'Current Tailscale device has no IPv4 address')
  const defaultPort = protocol === 'https' ? '443' : '80'
  const authority = servedPort === defaultPort ? hostname : `${hostname}:${servedPort}`
  const url = privateEntryUrl(`${protocol}://${authority}`)
  return { url, address }
}

function readOnlyGet(url, address = null) {
  const target = new URL(privateEntryUrl(url))
  const client = target.protocol === 'https:' ? https : http
  return new Promise((resolve, reject) => {
    const request = client.get(target, {
      headers: { Accept: 'text/html' },
      lookup: address
        ? (_hostname, options, callback) => {
          const family = net.isIP(address)
          if (options?.all) callback(null, [{ address, family }])
          else callback(null, address, family)
        }
        : undefined,
    }, response => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { body += chunk })
      response.on('end', () => resolve({ status: response.statusCode || 0, body }))
    })
    request.setTimeout(15000, () => request.destroy(new Error('Unauthenticated GET timed out')))
    request.on('error', reject)
  })
}

async function auditRemoteEntry(cdp, url, {
  origin,
  screenshotName = null,
  getUrl = url,
  address = null,
  assertAccessibilityContract = true,
}) {
  const target = privateEntryUrl(url)
  const response = await readOnlyGet(getUrl, address)
  assert(response.status >= 200 && response.status < 300, `${origin} did not accept an unauthenticated GET`)
  assert.match(response.body, /<title>Proxima<\/title>/i)

  const forwarded = []
  const blocked = []
  const errors = []
  const pending = new Set()
  const listener = event => {
    const task = (async () => {
      const request = event.request
      let requestUrl
      try {
        requestUrl = new URL(request.url)
      } catch {
        errors.push(new Error('Browser requested an invalid URL'))
        await cdp.send('Fetch.failRequest', {
          requestId: event.requestId,
          errorReason: 'BlockedByClient',
        })
        return
      }
      if (request.method === 'POST'
        && requestUrl.origin === new URL(target).origin
        && requestUrl.pathname === '/auth/resume') {
        blocked.push('POST /auth/resume')
        await cdp.send('Fetch.fulfillRequest', {
          requestId: event.requestId,
          responseCode: 401,
          responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
          body: Buffer.from(JSON.stringify({ detail: 'Not authenticated' })).toString('base64'),
        })
        return
      }
      if (request.method !== 'GET') {
        errors.push(new Error(`Browser attempted forbidden ${request.method} request`))
        await cdp.send('Fetch.failRequest', {
          requestId: event.requestId,
          errorReason: 'BlockedByClient',
        })
        return
      }
      forwarded.push('GET')
      await cdp.send('Fetch.continueRequest', { requestId: event.requestId })
    })().catch(error => {
      errors.push(error instanceof Error ? error : new Error(String(error)))
    })
    pending.add(task)
    void task.finally(() => pending.delete(task))
  }

  cdp.on('Fetch.requestPaused', listener)
  await cdp.send('Network.clearBrowserCookies')
  await cdp.send('Network.clearBrowserCache')
  await cdp.send('Fetch.enable', {
    patterns: [{ urlPattern: '*', requestStage: 'Request' }],
  })
  try {
    await navigate(cdp, target, 'Welcome back')
    await Promise.all([...pending])
  } finally {
    await cdp.send('Fetch.disable')
    cdp.off('Fetch.requestPaused', listener)
  }
  assert.deepEqual(errors, [])
  assert(forwarded.length > 0, `${origin} did not receive browser GET requests`)
  assert(blocked.length > 0, `${origin} did not exercise unauthenticated session resume`)
  assert(blocked.every(request => request === 'POST /auth/resume'))
  const state = await evaluate(cdp, `(() => ({
    title: document.title,
    mainCount: document.querySelectorAll('main').length,
    passwordInputCount: document.querySelectorAll('input[type=password]').length,
    passwordCount: document.querySelectorAll('input[autocomplete=current-password]').length,
    owner: document.querySelector('input[name=username]')?.value,
    authenticatedShell: Boolean(document.querySelector('.app-shell')),
  }))()`)
  assert.equal(state.title, 'Proxima')
  assert.equal(state.authenticatedShell, false)
  assert.equal(state.passwordInputCount, 1)
  if (assertAccessibilityContract) {
    assert.equal(state.mainCount, 1)
    assert.equal(state.passwordCount, 1)
    assert.equal(state.owner, 'owner')
  }
  if (screenshotName) await screenshot(cdp, screenshotName)
  return { origin, status: 'pass' }
}

function runLighthouse(baseUrl, environment) {
  const reportPath = path.join(evidenceDir, 'lighthouse.json')
  const result = spawnSync(lighthouse, [
    baseUrl,
    '--quiet',
    '--only-categories=accessibility',
    '--output=json',
    `--output-path=${reportPath}`,
    '--chrome-flags=--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --no-proxy-server',
  ], {
    cwd: repoRoot,
    env: { ...environment, CHROME_PATH: chromePath },
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024,
  })
  if (result.status !== 0) {
    throw new Error(`Lighthouse failed:\n${result.stdout}\n${result.stderr}`)
  }
  const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'))
  const score = Math.round((report.categories?.accessibility?.score || 0) * 100)
  assert.equal(score, 100, `Lighthouse accessibility score was ${score}`)
  return {
    score,
    version: report.lighthouseVersion,
    url: report.finalDisplayedUrl,
  }
}

function writeEvidence(report) {
  fs.writeFileSync(
    path.join(evidenceDir, 'report.json'),
    `${JSON.stringify(report, null, 2)}\n`,
  )
  const remote = `${report.tailscaleEntry.status} - ${report.tailscaleEntry.origin}`
  const markdown = `# Auth and onboarding accessibility evidence

This pass uses the production web bundle, a disposable owner database, and headless
Chrome at 1440 x 1000. The local flow does not read or alter live Proxima data.
The separate private-entry check sends unauthenticated GET requests only.

| Check | Result |
| --- | --- |
| First-run mismatch focus and single announcement | pass |
| Unsafe folder focus and single announcement | pass |
| Overlong display-name field routing | pass |
| Derived-slug collision field routing | pass |
| Pressed-button Tab and Space behavior | pass |
| Returning login failure and success | pass |
| Accessibility trees and one main landmark | pass |
| Every supported theme meets WCAG AA text contrast | pass |
| Lighthouse accessibility | ${report.lighthouse.score} |
| Isolated Tailnet-host GET-only unauthenticated entry | pass |
| Private Tailscale unauthenticated entry | ${remote} |

## Before and after

| Flow | Before | After |
| --- | --- | --- |
| Password gate | [tour capture](../../screenshots/first-run-password.png) | [setup mismatch](auth-setup-mismatch-after.png), [returning login](auth-login-error-after.png) |
| Folder onboarding | [legacy Link tab](../../screenshots/onboarding-link-folder.png), [legacy Create tab](../../screenshots/onboarding-create-folder.png) | [unsafe folder](onboarding-validation-after.png), [slug collision](onboarding-slug-collision-after.png) |
| Remote entry | - | [isolated Tailnet-host login](tailnet-unauthenticated-entry.png) |

Machine-readable details are in [report.json](report.json), with the full
[Lighthouse report](lighthouse.json). The private Tailscale origin is deliberately
redacted; only its label and passing state are retained.
`
  fs.writeFileSync(path.join(evidenceDir, 'README.md'), markdown)
}

async function main() {
  for (const required of [serve, webDist, lighthouse, chromePath]) {
    assert(fs.existsSync(required), `Required audit dependency is missing: ${required}`)
  }
  const configuredPython = process.env.PROXIMA_A11Y_PYTHON?.trim()
  const usePython = configuredPython || (fs.existsSync(venvPython) ? venvPython : null)
  const apiCommand = usePython || process.env.PROXIMA_A11Y_UV || 'uv'
  const apiArguments = usePython
    ? [serve]
    : ['run', '--directory', apiRoot, 'python', serve]
  const privateTailscale = discoverPrivateTailscaleEntry(allowlistedEnvironment())
  fs.mkdirSync(evidenceDir, { recursive: true })

  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'proxima-a11y-'))
  const apiPort = await freePort()
  const cdpPort = await freePort()
  const baseUrl = `http://127.0.0.1:${apiPort}/`
  const tailnetFixtureUrl = `http://proxima.tailnet.test:${apiPort}/`
  const chromeProfile = path.join(fixtureRoot, 'chrome')
  fs.mkdirSync(chromeProfile, { recursive: true })
  const fixtureHome = path.join(fixtureRoot, 'home')
  const dataRoot = path.join(fixtureRoot, 'data')
  const workspaceRoot = path.join(dataRoot, 'workspace')
  for (const directory of [
    fixtureHome,
    workspaceRoot,
    path.join(fixtureRoot, 'tmp'),
    path.join(fixtureRoot, 'cache'),
    path.join(fixtureRoot, 'config'),
    path.join(fixtureRoot, 'uv-cache'),
  ]) {
    fs.mkdirSync(directory, { recursive: true })
  }
  const apiEnvironment = disposableApiEnvironment({
    fixtureRoot,
    fixtureHome,
    dataRoot,
    workspaceRoot,
    apiPort,
  })
  const browserEnvironment = allowlistedEnvironment({
    HOME: fixtureHome,
    TMPDIR: path.join(fixtureRoot, 'tmp'),
    XDG_CACHE_HOME: path.join(fixtureRoot, 'cache'),
    XDG_CONFIG_HOME: path.join(fixtureRoot, 'config'),
    XDG_DATA_HOME: dataRoot,
  })
  const hostResolverRules = ['MAP proxima.tailnet.test 127.0.0.1']
  if (privateTailscale.address) {
    hostResolverRules.push(`MAP ${new URL(privateTailscale.url).hostname} ${privateTailscale.address}`)
  }
  let serverLog = ''
  let chromeLog = ''
  const api = spawn(apiCommand, apiArguments, {
    cwd: repoRoot,
    env: apiEnvironment,
    stdio: ['ignore', 'ignore', 'pipe'],
  })
  api.stderr.on('data', chunk => { serverLog = `${serverLog}${chunk}`.slice(-12000) })

  const chrome = spawn(chromePath, [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--no-proxy-server',
    '--disable-background-networking',
    `--host-resolver-rules=${hostResolverRules.join(',')}`,
    `--user-data-dir=${chromeProfile}`,
    `--remote-debugging-port=${cdpPort}`,
    '--window-size=1440,1000',
    baseUrl,
  ], {
    env: browserEnvironment,
    stdio: ['ignore', 'ignore', 'pipe'],
  })
  chrome.stderr.on('data', chunk => { chromeLog = `${chromeLog}${chunk}`.slice(-12000) })

  try {
    await waitForJson(
      `${baseUrl}api/health`,
      value => value?.ok === true && value?.database === 'ok',
      'Disposable Proxima API',
    )
    const cdp = await connectCdp(cdpPort, baseUrl)
    await cdp.send('Runtime.enable')
    await cdp.send('Page.enable')
    await cdp.send('Network.enable')
    await cdp.send('Accessibility.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1440,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false,
    })

    await navigate(cdp, baseUrl, 'Set a password')
    await evaluate(cdp, `localStorage.setItem('proxima.theme', 'light'); location.reload(); true`)
    await waitForPage(cdp, `document.querySelector('h1')?.textContent === 'Set a password'`, 'Light setup gate')
    assert.equal(await evaluate(cdp, `document.querySelectorAll('main').length`), 1)
    assert.deepEqual(await evaluate(cdp, `(() => {
      const owner = document.querySelector('input[name=username]')
      return {
        value: owner?.value,
        readOnly: owner?.readOnly,
        autocomplete: owner?.autocomplete,
        tabIndex: owner?.tabIndex,
      }
    })()`), {
      value: 'owner',
      readOnly: true,
      autocomplete: 'username',
      tabIndex: -1,
    })

    await setInput(cdp, 'password', 'longenough1')
    await setInput(cdp, 'password-confirmation', 'different99')
    await startAnnouncementTrace(cdp)
    await clickButton(cdp, 'Set password & enter')
    await waitForPage(cdp, `document.querySelector('[role=alert]')?.textContent.includes('Passwords')`, 'Mismatch error')
    const mismatchTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(mismatchTrace, 'password-confirmation', /Passwords.*match/)
    const mismatchAx = await accessibilitySummary(cdp)
    assert.equal(mismatchAx.mains.length, 1)
    assert.equal(mismatchAx.alerts.length, 1)
    assert.match(mismatchAx.alerts[0].text, /Passwords.*match/)
    assert(mismatchAx.focused.some(node => node.name === 'Confirm password' && node.invalid))
    await screenshot(cdp, 'auth-setup-mismatch-after.png')

    await setInput(cdp, 'password-confirmation', 'longenough1')
    await clickButton(cdp, 'Set password & enter')
    await waitForPage(cdp, `document.querySelector('h1')?.textContent === 'Pick your working folder'`, 'Onboarding')
    assert.equal(await evaluate(cdp, `document.querySelectorAll('main').length`), 1)

    await focusButton(cdp, 'Link existing')
    await pressKey(cdp, 'Tab', 'Tab', 9)
    assert.equal(await evaluate(cdp, `document.activeElement?.textContent.trim()`), 'Create new folder')
    await pressKey(cdp, ' ', 'Space', 32, ' ')
    await waitForPage(cdp, `document.querySelector('button[aria-pressed=true]')?.textContent.trim() === 'Create new folder'`, 'Create mode')

    await setInput(cdp, 'folder-name', 'bad/name')
    await startAnnouncementTrace(cdp)
    await clickButton(cdp, 'Create “bad/name” here')
    await waitForPage(cdp, `document.querySelector('[role=alert]')?.textContent.includes('cannot contain slashes')`, 'Unsafe folder error')
    const folderTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(folderTrace, 'folder-name', /cannot contain slashes/)
    const folderAx = await accessibilitySummary(cdp)
    assert.equal(folderAx.mains.length, 1)
    assert.equal(folderAx.alerts.length, 1)
    assert.equal(folderAx.tabs.length, 0)
    assert(folderAx.buttons.some(node => node.name === 'Create new folder' && node.pressed === 'true'))
    assert(folderAx.focused.some(node => node.name.includes('New folder name') && node.invalid))
    await screenshot(cdp, 'onboarding-validation-after.png')

    await setInput(cdp, 'folder-name', 'valid-folder')
    await setInput(cdp, 'project-display-name', 'x'.repeat(121))
    await startAnnouncementTrace(cdp)
    await clickButton(cdp, 'Create “valid-folder” here')
    await waitForPage(cdp, `document.querySelector('[role=alert]')?.textContent.includes('120 characters')`, 'Display-name error')
    const displayTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(displayTrace, 'project-display-name', /120 characters/)
    assert.equal(await evaluate(cdp, `document.activeElement?.name`), 'project-display-name')
    assert.equal(await evaluate(cdp, `document.querySelector('input[name=folder-name]').getAttribute('aria-invalid')`), null)

    const reserved = await evaluate(cdp, `(async () => {
      const response = await fetch('/api/projects/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: ${JSON.stringify(path.join(fixtureHome, 'reserved-project'))},
          name: 'Shared Name',
          mkdir: true,
        }),
      })
      return { status: response.status, body: await response.text() }
    })()`)
    assert.equal(reserved.status, 201, reserved.body)
    await setInput(cdp, 'folder-name', 'different-folder')
    await setInput(cdp, 'project-display-name', 'Shared Name')
    const collisionFocusedBeforeError = await startAnnouncementTrace(cdp)
    await clickButton(cdp, 'Create “different-folder” here')
    await waitForPage(cdp, `document.querySelector('[role=alert]')?.textContent.includes('already exists')`, 'Slug collision error')
    const collisionTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      collisionTrace,
      'project-display-name',
      /already exists/,
      collisionFocusedBeforeError === 'project-display-name',
    )
    assert.equal(await evaluate(cdp, `document.activeElement?.name`), 'project-display-name')
    assert.equal(await evaluate(cdp, `document.querySelector('input[name=folder-name]').getAttribute('aria-invalid')`), null)
    await screenshot(cdp, 'onboarding-slug-collision-after.png')

    await clickButton(cdp, 'Skip for now')
    await waitForPage(cdp, `Boolean(document.querySelector('.app-shell'))`, 'Authenticated shell')
    await cdp.send('Network.clearBrowserCookies')
    await navigate(cdp, baseUrl, 'Welcome back')

    const themeResults = []
    for (const theme of themes) themeResults.push(await auditTheme(cdp, theme))
    await evaluate(cdp, `localStorage.setItem('proxima.theme', 'light'); location.reload(); true`)
    await waitForPage(cdp, `document.querySelector('h1')?.textContent === 'Welcome back'`, 'Light returning login')
    await setInput(cdp, 'password', 'wrong-password')
    const loginFocusedBeforeError = await startAnnouncementTrace(cdp)
    await clickButton(cdp, 'Log in')
    await waitForPage(cdp, `document.querySelector('[role=alert]')?.textContent.includes('Incorrect password')`, 'Login error')
    const loginTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      loginTrace,
      'password',
      /Incorrect password/,
      loginFocusedBeforeError === 'password',
    )
    const loginAx = await accessibilitySummary(cdp)
    assert.equal(loginAx.mains.length, 1)
    assert.equal(loginAx.alerts.length, 1)
    assert(loginAx.focused.some(node => node.name === 'Password' && node.invalid))
    await screenshot(cdp, 'auth-login-error-after.png')

    await setInput(cdp, 'password', 'longenough1')
    await clickButton(cdp, 'Log in')
    await waitForPage(cdp, `Boolean(document.querySelector('.app-shell'))`, 'Returning login success')

    const tailnetFixture = await auditRemoteEntry(
      cdp,
      tailnetFixtureUrl,
      {
        origin: 'isolated Tailnet-host fixture',
        screenshotName: 'tailnet-unauthenticated-entry.png',
        getUrl: baseUrl,
      },
    )
    const tailscaleEntry = await auditRemoteEntry(
      cdp,
      privateTailscale.url,
      {
        origin: 'private Tailscale origin (redacted)',
        address: privateTailscale.address,
        assertAccessibilityContract: false,
      },
    )
    const lighthouseResult = runLighthouse(baseUrl, browserEnvironment)
    const report = {
      viewport: { width: 1440, height: 1000 },
      runtime: { node: process.version },
      fixture: 'disposable production bundle and owner database',
      isolation: {
        environment: 'allowlisted',
        writableRoots: 'disposable fixture only',
        backgroundWorker: 'disabled',
        liveServiceWrites: 'disabled',
      },
      announcements: {
        setupMismatch: mismatchTrace,
        unsafeFolder: folderTrace,
        overlongDisplayName: displayTrace,
        derivedSlugCollision: collisionTrace,
        returningLogin: loginTrace,
      },
      accessibilityTrees: {
        setupMismatch: mismatchAx,
        unsafeFolder: folderAx,
        returningLogin: loginAx,
      },
      themes: themeResults,
      tailnetFixture,
      tailscaleEntry,
      lighthouse: lighthouseResult,
    }
    writeEvidence(report)
    process.stdout.write(`accessibility audit: pass, Lighthouse ${lighthouseResult.score}\n`)
  } catch (error) {
    throw new Error(`${error.stack || error}\nAPI log:\n${serverLog}\nChrome log:\n${chromeLog}`)
  } finally {
    chrome.kill('SIGTERM')
    api.kill('SIGTERM')
    await sleep(300)
    fs.rmSync(fixtureRoot, { recursive: true, force: true })
  }
}

main().catch(error => {
  process.stderr.write(`${error.stack || error}\n`)
  process.exitCode = 1
})
