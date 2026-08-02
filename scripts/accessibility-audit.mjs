#!/usr/bin/env node

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import { createRequire } from 'node:module'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  assertCorrectiveAlertText,
  assertServiceWorkerCacheMatrix,
  GATE_TEXT_STYLES,
  privateEntryUrl,
  resolvePrivateTailscaleEntry,
  summarizeStaticShellRequests,
} from './accessibility-audit-policy.mjs'
import { RemoteEntryInterceptor } from './remote-entry-interceptor.mjs'

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

function canonicalServiceWorkerShellPaths() {
  const source = fs.readFileSync(path.join(webRoot, 'public', 'sw.js'), 'utf8')
  assert.doesNotMatch(source, /\b(?:WebSocket|EventSource)\b/)
  const cache = source.match(/const CACHE\s*=\s*['"]([^'"]+)['"]/)
  const catalog = source.match(/const APP_SHELL\s*=\s*\[([^\]]+)\]/)
  assert(cache && catalog, 'Could not read the production service-worker cache contract')
  const paths = [...catalog[1].matchAll(/['"]([^'"]+)['"]/g)]
    .map(match => match[1])
  assert(paths.length > 0, 'Production service-worker APP_SHELL is empty')
  assert(paths.every(item => !/^\/(?:api|auth)\//.test(item)))
  return {
    cacheName: cache[1],
    digest: createHash('sha256').update(source).digest('hex'),
    paths,
    transportSafe: true,
  }
}

const serviceWorkerArtifact = canonicalServiceWorkerShellPaths()
const serviceWorkerShellPaths = serviceWorkerArtifact.paths

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
    const rejectPending = reason => {
      const error = reason instanceof Error
        ? reason
        : new Error('Chrome DevTools connection closed')
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timeout)
        pending.reject(error)
      }
      this.pending.clear()
    }
    socket.onmessage = event => {
      const message = JSON.parse(event.data)
      if (message.id && this.pending.has(message.id)) {
        const pending = this.pending.get(message.id)
        this.pending.delete(message.id)
        clearTimeout(pending.timeout)
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)))
        else pending.resolve(message.result)
        return
      }
      for (const listener of this.listeners.get(message.method) || []) {
        listener(message.params, message.sessionId)
      }
    }
    socket.onerror = rejectPending
    socket.onclose = rejectPending
  }

  send(method, params = {}, sessionId = null, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const id = ++this.nextId
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`Timed out sending ${method}`))
      }, timeoutMs)
      this.pending.set(id, { resolve, reject, timeout })
      try {
        this.socket.send(JSON.stringify({
          id,
          method,
          params,
          ...(sessionId ? { sessionId } : {}),
        }))
      } catch (error) {
        clearTimeout(timeout)
        this.pending.delete(id)
        reject(error)
      }
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

  close() {
    const error = new Error('Chrome DevTools connection closed')
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timeout)
      pending.reject(error)
    }
    this.pending.clear()
    this.socket.close()
  }
}

async function connectSocket(webSocketDebuggerUrl) {
  const socket = new WebSocket(webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  return new CdpClient(socket)
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
  const cdp = await connectSocket(page.webSocketDebuggerUrl)
  cdp.targetId = page.id
  return cdp
}

async function connectBrowserCdp(port) {
  const browser = await waitForJson(
    `http://127.0.0.1:${port}/json/version`,
    value => Boolean(value?.webSocketDebuggerUrl),
    'Chrome browser target',
  )
  return connectSocket(browser.webSocketDebuggerUrl)
}

function launchChrome({
  profile,
  port,
  initialUrl,
  hostResolverRules,
  environment,
  onStderr,
}) {
  const chrome = spawn(chromePath, [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--no-proxy-server',
    '--disable-background-networking',
    `--host-resolver-rules=${hostResolverRules.join(',')}`,
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    '--window-size=1440,1000',
    initialUrl,
  ], {
    env: environment,
    stdio: ['ignore', 'ignore', 'pipe'],
  })
  chrome.stderr.on('data', onStderr)
  return chrome
}

async function initializePageCdp(cdp) {
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

async function activateButtonByKeyboard(cdp, label, key, code, keyCode, text = '') {
  await focusButton(cdp, label)
  assert.equal(
    await evaluate(cdp, `document.activeElement?.textContent.trim()`),
    label,
    `${label} did not hold keyboard focus`,
  )
  await startAnnouncementTrace(cdp)
  await pressKey(cdp, key, code, keyCode, text)
}

async function refreshSelectedFolder(cdp) {
  const refreshed = await evaluate(cdp, `(() => {
    const button = document.querySelector('button[name=selected-folder]')
    if (!(button instanceof HTMLButtonElement)) return false
    button.click()
    return true
  })()`)
  assert(refreshed, 'Missing selected-folder recovery control')
}

async function failNextFolderBrowse(cdp) {
  let resolveIntercepted
  let rejectIntercepted
  const intercepted = new Promise((resolve, reject) => {
    resolveIntercepted = resolve
    rejectIntercepted = reject
  })
  let handled = false
  const listener = event => {
    if (handled) return
    handled = true
    cdp.send('Fetch.fulfillRequest', {
      requestId: event.requestId,
      responseCode: 403,
      responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
      body: Buffer.from(JSON.stringify({
        detail: {
          message: 'No readable folder is available inside the allowed roots',
          field: 'path',
        },
      })).toString('base64'),
    })
      .then(() => cdp.send('Fetch.disable'))
      .then(resolveIntercepted, rejectIntercepted)
  }
  cdp.on('Fetch.requestPaused', listener)
  await cdp.send('Fetch.enable', {
    patterns: [{ urlPattern: '*api/fs/dirs*', requestStage: 'Request' }],
  })
  return {
    intercepted,
    stop: async () => {
      cdp.off('Fetch.requestPaused', listener)
      if (!handled) await cdp.send('Fetch.disable')
    },
  }
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
    assertCorrectiveAlertText(relevant[0]?.name || '', messagePattern)
    return
  }
  assert.equal(relevant[0]?.type, 'focus')
  assert.equal(relevant[0]?.name, fieldName)
  assert.equal(relevant[1]?.type, 'alert')
  assertCorrectiveAlertText(relevant[1]?.name || '', messagePattern)
}

function assertSingleSemanticOwner(summary, targetPredicate, messagePattern) {
  assert.equal(summary.alerts.length, 1)
  assertCorrectiveAlertText(summary.alerts[0].text, messagePattern)
  const target = summary.focused.find(targetPredicate)
  assert(target, 'Corrective target was not focused in the accessibility tree')
  assert(target.invalid, 'Corrective target was not marked invalid')
  assert.equal(target.description, '', 'Corrective target duplicated the alert as its description')
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
        const parts = rgb[1].split(/[ ,/]+/).filter(Boolean)
        const channels = parts.slice(0, 3).map(part => part.endsWith('%') ? parseFloat(part) / 100 : Number(part) / 255)
        const alpha = parts[3] == null ? 1 : parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : Number(parts[3])
        return [...channels, alpha]
      }
      const srgb = value.match(/^color\\(srgb\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s/)]+)(?:\\s*\\/\\s*([^\\s)]+))?\\)$/)
      if (srgb) return [...srgb.slice(1, 4).map(Number), srgb[4] == null ? 1 : Number(srgb[4])]
      throw new Error('Unsupported computed color: ' + value)
    }
    function composite(top, bottom) {
      return [
        (top[0] * top[3]) + (bottom[0] * (1 - top[3])),
        (top[1] * top[3]) + (bottom[1] * (1 - top[3])),
        (top[2] * top[3]) + (bottom[2] * (1 - top[3])),
        1,
      ]
    }
    function luminance(color) {
      const [red, green, blue] = color.slice(0, 3).map(channel)
      return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
    }
    function contrast(foreground, background, backdrop = background) {
      const base = composite(parseColor(background), parseColor(backdrop))
      const text = composite(parseColor(foreground), base)
      const a = luminance(text)
      const b = luminance(base)
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
    }
    const card = document.querySelector('.auth-card')
    const title = document.querySelector('.auth-title')
    const subtitle = document.querySelector('.auth-sub')
    const error = document.querySelector('.auth-error')
    const button = document.querySelector('.auth-submit')
    const password = document.querySelector('input[name=password]')
    const cardStyle = getComputedStyle(card)
    const titleStyle = getComputedStyle(title)
    const subtitleStyle = getComputedStyle(subtitle)
    const errorStyle = getComputedStyle(error)
    const buttonStyle = getComputedStyle(button)
    const inputStyle = getComputedStyle(password)
    const placeholderStyle = getComputedStyle(password, '::placeholder')
    const inputFocusStyle = getComputedStyle(password)
    return {
      theme: document.documentElement.dataset.theme,
      textContrast: {
        title: contrast(titleStyle.color, cardStyle.backgroundColor),
        subtitle: contrast(subtitleStyle.color, cardStyle.backgroundColor),
        inputValue: contrast(inputStyle.color, inputStyle.backgroundColor, cardStyle.backgroundColor),
        placeholder: contrast(placeholderStyle.color, inputStyle.backgroundColor, cardStyle.backgroundColor),
        error: contrast(errorStyle.color, cardStyle.backgroundColor),
        button: contrast(buttonStyle.color, buttonStyle.backgroundColor, cardStyle.backgroundColor),
      },
      focus: {
        input: {
          style: inputFocusStyle.outlineStyle,
          width: parseFloat(inputFocusStyle.outlineWidth),
          color: inputFocusStyle.outlineColor,
          contrast: contrast(inputFocusStyle.outlineColor, cardStyle.backgroundColor),
        },
      },
    }
  })()`)
  await pressKey(cdp, 'Tab', 'Tab', 9)
  result.focus.button = await evaluate(cdp, `(() => {
    function channel(value) {
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    }
    function parseColor(value) {
      const rgb = value.match(/^rgba?\\(([^)]+)\\)$/)
      if (!rgb) throw new Error('Unsupported computed color: ' + value)
      const parts = rgb[1].split(/[ ,/]+/).filter(Boolean)
      return parts.slice(0, 3).map(part => part.endsWith('%') ? parseFloat(part) / 100 : Number(part) / 255)
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
    const button = document.querySelector('.auth-submit')
    const card = document.querySelector('.auth-card')
    const style = getComputedStyle(button)
    return {
      focused: document.activeElement === button,
      style: style.outlineStyle,
      width: parseFloat(style.outlineWidth),
      color: style.outlineColor,
      contrast: contrast(style.outlineColor, getComputedStyle(card).backgroundColor),
    }
  })()`)
  assert.equal(result.theme, theme)
  assert.deepEqual(Object.keys(result.textContrast), GATE_TEXT_STYLES)
  for (const [style, ratio] of Object.entries(result.textContrast)) {
    assert(ratio >= 4.5, `${theme} ${style} contrast is ${ratio}`)
  }
  for (const [control, focus] of Object.entries(result.focus)) {
    assert.notEqual(focus.style, 'none', `${theme} ${control} focus is invisible`)
    assert(focus.width >= 2, `${theme} ${control} focus width is ${focus.width}`)
    assert(focus.contrast >= 3, `${theme} ${control} focus contrast is ${focus.contrast}`)
  }
  assert.equal(result.focus.button.focused, true, `${theme} button did not receive keyboard focus`)
  return result
}

function discoverPrivateTailscaleEntry(environment) {
  const configured = process.env.PROXIMA_A11Y_REMOTE_BASE?.trim()
  const configuredAddress = process.env.PROXIMA_A11Y_REMOTE_ADDRESS?.trim()
  const proxyPort = process.env.PROXIMA_A11Y_REMOTE_PROXY_PORT?.trim() || '8765'
  const command = process.env.PROXIMA_A11Y_TAILSCALE_BIN?.trim() || 'tailscale'
  const serveResult = spawnSync(command, ['serve', 'status', '--json'], {
    env: environment,
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
  })
  assert.equal(serveResult.status, 0, 'Could not read the current Tailscale Serve configuration')
  const deviceResult = spawnSync(command, ['status', '--json'], {
    env: environment,
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
  })
  assert.equal(deviceResult.status, 0, 'Could not read the current Tailscale device address')
  return resolvePrivateTailscaleEntry({
    serveStatus: JSON.parse(serveResult.stdout),
    deviceStatus: JSON.parse(deviceResult.stdout),
    proxyPort,
    configuredBase: configured || '',
    configuredAddress: configuredAddress || '',
  })
}

async function closeInspectedTargets(browserCdp, interceptor, pageTargetId) {
  const targetIds = new Set([pageTargetId])
  const deadline = Date.now() + 5000
  let emptySince = null
  while (Date.now() <= deadline) {
    for (const targetId of interceptor.inspectedTargetIds()) {
      targetIds.add(targetId)
    }
    const remaining = await browserCdp.send('Target.getTargets', {}, null, 1000)
    const remainingIds = new Set(
      (remaining.targetInfos || []).map(targetInfo => targetInfo.targetId),
    )
    const liveTargetIds = [...targetIds].filter(targetId => remainingIds.has(targetId))
    if (liveTargetIds.length === 0) {
      emptySince ??= Date.now()
      if (Date.now() - emptySince >= Math.max(interceptor.quietMs, 100)) return
    } else {
      emptySince = null
      for (const targetId of liveTargetIds) {
        await browserCdp.send(
          'Target.closeTarget',
          { targetId },
          null,
          1000,
        ).catch(() => null)
      }
    }
    await sleep(25)
  }
  throw new Error('Remote entry left an inspected page or worker target open')
}

async function auditRemoteEntry({
  pageCdp,
  browserCdp,
  pageTargetId,
}, url, {
  origin,
  screenshotName = null,
  provenance = null,
  assertAccessibilityContract = true,
  proveServiceWorkerArtifact = false,
}) {
  const target = privateEntryUrl(url)
  const targetOrigin = new URL(target).origin
  let serviceWorkerPreverified = false
  if (proveServiceWorkerArtifact) {
    const response = await fetch(new URL('/sw.js', target), {
      headers: { 'Cache-Control': 'no-store' },
    })
    assert.equal(response.status, 200, `${origin} service-worker proof GET failed`)
    const body = Buffer.from(await response.arrayBuffer())
    assert.equal(
      createHash('sha256').update(body).digest('hex'),
      serviceWorkerArtifact.digest,
      `${origin} service-worker proof differs from the audited artifact`,
    )
    serviceWorkerPreverified = true
  }
  const interceptor = new RemoteEntryInterceptor({
    cdp: browserCdp,
    pageTargetId,
    targetOrigin,
    serviceWorkerDigest: serviceWorkerArtifact.digest,
    serviceWorkerTransportSafe: serviceWorkerArtifact.transportSafe,
    serviceWorkerPreverified,
  })
  await pageCdp.send('Network.clearBrowserCookies')
  await pageCdp.send('Network.clearBrowserCache')
  await interceptor.start()
  let serviceWorkerExpected = false
  let pageClosed = false
  let interception = null
  let auditFailed = false
  try {
    await navigate(pageCdp, target, 'Welcome back')
    serviceWorkerExpected = await evaluate(
      pageCdp,
      `window.isSecureContext
        && 'serviceWorker' in navigator
        && ![...document.scripts].some(script =>
          script.src.includes('/@vite/client') || script.src.includes('/src/')
        )`,
    )
    await interceptor.waitForSettled()
    const state = await evaluate(pageCdp, `(() => ({
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
    if (screenshotName) await screenshot(pageCdp, screenshotName)

    let serviceWorkerCache = null
    if (serviceWorkerExpected) {
      serviceWorkerCache = await evaluate(
        pageCdp,
        `(async () => {
          await navigator.serviceWorker.ready
          const cacheNames = await caches.keys()
          const cache = await caches.open(${JSON.stringify(serviceWorkerArtifact.cacheName)})
          const requests = await cache.keys()
          return {
            cacheNames,
            paths: requests.map(request => {
              const url = new URL(request.url)
              return url.pathname + url.search
            }),
          }
        })()`,
      )
      assert.deepEqual(
        serviceWorkerCache.cacheNames,
        [serviceWorkerArtifact.cacheName],
        `${origin} service-worker cache names differ from the audited artifact`,
      )
    }

    interceptor.beginClosure()
    await closeInspectedTargets(browserCdp, interceptor, pageTargetId)
    pageClosed = true
    await interceptor.waitForSettled()
    interception = interceptor.snapshot()

    const forwardedLabels = interception.forwarded.map(request => request.label)
    const fulfilledLabels = interception.fulfilled.map(request => request.label)
    assert(
      interception.forwarded.length > 0,
      `${origin} did not receive static shell GET requests`,
    )
    assert(forwardedLabels.every(
      request => !request.startsWith('GET /api/') && !request.startsWith('GET /auth/'),
    ))
    if (serviceWorkerExpected) {
      assert(
        interception.targetTypes.includes('service_worker'),
        `${origin} service worker was not attached and inspected`,
      )
      assert(
        interception.verifiedServiceWorkerCount > 0,
        `${origin} service worker response was not verified`,
      )
    }
    const shellRequests = summarizeStaticShellRequests(interception.forwarded)
    let workerEvidence = null
    if (serviceWorkerExpected) {
      workerEvidence = assertServiceWorkerCacheMatrix(
        serviceWorkerCache.paths,
        serviceWorkerShellPaths,
        interception.serviceWorkerProofGetCount,
      )
    }
    const fulfilledSet = new Set(fulfilledLabels)
    const bootstrapLabels = [
      'GET /api/config',
      'GET /api/setup/status',
      'POST /auth/resume',
    ]
    assert(bootstrapLabels.every(label => fulfilledSet.has(label)))
    assert([...fulfilledSet].every(label => (
      bootstrapLabels.includes(label) || label === 'GET /@vite/client'
    )))
    assert.equal(interception.webSocket.handshakeRequestCount, 0)
    assert.equal(interception.webSocket.handshakeResponseCount, 0)
    assert.equal(interception.webSocket.framesSent, 0)
    assert.equal(interception.webSocket.framesReceived, 0)
    assert.equal(
      interception.webSocket.blockedCount,
      interception.webSocket.attemptedCount,
    )
    assert.equal(
      interception.webSocket.failureCount,
      interception.webSocket.attemptedCount,
    )
    return {
      origin,
      status: 'pass',
      ...(provenance ? { provenance } : {}),
      network: {
        forwarded: 'same-origin static shell GETs from attached page and worker targets only',
        shellRootGetCount: shellRequests.rootGetCount,
        pageRootGetCount: shellRequests.pageRootGetCount,
        forwardedStaticGetCount: shellRequests.staticGetCount,
        forwardedByTargetType: shellRequests.targetTypeCounts,
        rootGetsByTargetType: shellRequests.rootGetCountByTargetType,
        requestCountsByTargetType: shellRequests.requestCountsByTargetType,
        observedTargetTypes: interception.targetTypes,
        serviceWorker: workerEvidence
          ? {
              artifact: 'verified against audited source',
              ...workerEvidence,
            }
          : 'not requested',
        transportPolicies: interception.transportPolicies,
        bootstrap: 'fulfilled in browser fixture',
        viteRuntime: fulfilledSet.has('GET /@vite/client')
          ? 'inert browser fixture'
          : 'not requested',
        liveDataRequests: 'blocked through page and worker shutdown',
        blockedRequestCount: interception.blocked.length,
        webSocket: interception.webSocket,
      },
    }
  } catch (error) {
    auditFailed = true
    const state = pageClosed
      ? null
      : await evaluate(pageCdp, `(() => ({
        title: document.title,
        heading: document.querySelector('h1')?.textContent || '',
        text: document.body?.innerText?.slice(0, 240) || '',
        scripts: document.scripts.length,
      }))()`).catch(() => null)
    const observed = interception || interceptor.snapshot()
    throw new Error(
      `${origin} static shell audit failed: `
      + `${JSON.stringify({
        state,
        forwarded: [...new Set(observed.forwarded.map(request => request.label))].sort(),
        fulfilled: [...new Set(observed.fulfilled.map(request => request.label))].sort(),
        blocked: [...new Set(observed.blocked.map(request => request.label))].sort(),
        targetTypes: observed.targetTypes,
        webSocket: observed.webSocket,
      })}; ${error}`,
    )
  } finally {
    if (!pageClosed) {
      interceptor.beginClosure()
      await closeInspectedTargets(
        browserCdp,
        interceptor,
        pageTargetId,
      ).catch(() => null)
      await interceptor.waitForSettled().catch(() => null)
    }
    await interceptor.stop().catch(error => {
      if (!auditFailed) throw error
    })
  }
}

let remoteBrowserSequence = 0

async function auditRemoteEntryInIsolatedBrowser(url, options, {
  fixtureRoot,
  browserEnvironment,
  hostResolverRules,
  onChromeLog,
}) {
  remoteBrowserSequence += 1
  const profile = path.join(fixtureRoot, `remote-chrome-${remoteBrowserSequence}`)
  fs.mkdirSync(profile, { recursive: true })
  const port = await freePort()
  const chrome = launchChrome({
    profile,
    port,
    initialUrl: 'about:blank',
    hostResolverRules,
    environment: browserEnvironment,
    onStderr: onChromeLog,
  })
  let pageCdp = null
  let browserCdp = null
  try {
    pageCdp = await connectCdp(port, 'about:blank')
    browserCdp = await connectBrowserCdp(port)
    await initializePageCdp(pageCdp)
    return await auditRemoteEntry({
      pageCdp,
      browserCdp,
      pageTargetId: pageCdp.targetId,
    }, url, options)
  } finally {
    pageCdp?.close()
    browserCdp?.close()
    chrome.kill('SIGTERM')
    await sleep(300)
  }
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
  const remote = `${report.tailscaleEntry.status} - ${report.tailscaleEntry.origin}; current device Serve mapping verified (redacted)`
  const markdown = `# Auth and onboarding accessibility evidence

This pass uses the production web bundle, a disposable owner database, and headless
Chrome at 1440 x 1000. The local flow does not read or alter live Proxima data.
The command also runs focused API regressions for error ownership, readable-ancestor
selection, explicit no-ancestor failure, and the configured-root jail.
The private-entry browser check runs in an isolated profile, secures every page and
worker session before resume, accounts for every shell GET, and verifies the current
device Serve mapping. One session owns each target; a secured successor is promoted on
detach, and losing the last owner before audited closure fails the pass. The served
service worker must match the audited static-only source exactly. Its complete Cache
Storage key set must equal APP_SHELL. One explicit unauthenticated read-only
\`/sw.js\` GET proves the artifact before any worker resumes.
A service-worker target without the CDP Network domain stays paused until that served
digest matches the locally audited duplex-free artifact; Fetch interception remains
active for every request.
A development-served entry receives an inert no-socket Vite client fixture, and any
remaining outbound WebSocket handshake or frame fails the audit.

| Check | Result |
| --- | --- |
| First-run mismatch focus and single announcement | pass |
| Repeated mismatch gets one fresh announcement | pass |
| Unsafe folder focus and single announcement | pass |
| Repeated unsafe folder gets one fresh announcement with Enter | pass |
| Repeated unsafe folder gets one fresh announcement with Space | pass |
| Initial folder-load failure focuses an announced retry target | pass |
| Overlong display-name field routing | pass |
| Derived-slug collision field routing | pass |
| Missing create parent focuses selected-folder recovery | pass |
| Permission-denied create parent focuses selected-folder recovery | pass |
| Unreadable selection recovers to its nearest readable ancestor | pass |
| No readable ancestor retains explicit invalid state | pass |
| Browse recovery handles symlink cycles and remains inside configured roots | pass |
| Opaque root identity preserves symlink-alias ownership | pass |
| Folder names respect the target filesystem component byte limit | pass |
| Atomic folder publication and identity-safe rollback | pass |
| Missing selected folder focuses its refresh/reselect control | pass |
| Corrective targets and alerts have one semantic announcement owner | pass |
| Pressed-button Tab and Space behavior | pass |
| Returning login failure and success | pass |
| Accessibility trees and one main landmark | pass |
| Every gate text style in every supported theme meets WCAG AA contrast | pass |
| Input and button focus are visible in every supported theme | pass |
| Lighthouse accessibility | ${report.lighthouse.score} |
| Served service-worker identity and complete cache-key accounting | pass |
| Isolated Tailnet-host GET-only unauthenticated entry | pass |
| Remote browser accounts for page and worker shell GETs | pass |
| Remote browser blocks and accounts for WebSocket attempts | pass |
| Private Tailscale unauthenticated entry | ${remote} |

## Before and after

| Flow | Before | After |
| --- | --- | --- |
| Password gate | [tour capture](../../screenshots/first-run-password.png) | [setup mismatch](auth-setup-mismatch-after.png), [returning login](auth-login-error-after.png) |
| Folder onboarding | [legacy Link tab](../../screenshots/onboarding-link-folder.png), [legacy Create tab](../../screenshots/onboarding-create-folder.png) | [initial folder-load recovery](onboarding-folder-load-error-after.png), [unsafe folder](onboarding-validation-after.png), [slug collision](onboarding-slug-collision-after.png), [missing create parent](onboarding-create-parent-error-after.png), [permission-denied parent](onboarding-parent-permission-after.png), [missing selected folder](onboarding-path-error-after.png) |
| Remote entry | - | [isolated Tailnet-host login](tailnet-unauthenticated-entry.png) |

Machine-readable details are in [report.json](report.json), with the full
[Lighthouse report](lighthouse.json). The private Tailscale origin is deliberately
redacted; only its passing state and redacted current-device Serve provenance are retained.
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
  const vanishingFolder = path.join(fixtureHome, 'vanishing-folder')
  const disappearingParent = path.join(fixtureHome, 'disappearing-parent')
  const permissionDeniedParent = path.join(fixtureHome, 'permission-denied-parent')
  const noReadableParent = path.join(fixtureHome, 'no-readable-parent')
  for (const directory of [
    vanishingFolder,
    disappearingParent,
    permissionDeniedParent,
    noReadableParent,
  ]) {
    fs.mkdirSync(directory)
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

  const recordChromeLog = chunk => {
    chromeLog = `${chromeLog}${chunk}`.slice(-12000)
  }
  const chrome = launchChrome({
    profile: chromeProfile,
    port: cdpPort,
    initialUrl: baseUrl,
    hostResolverRules,
    environment: browserEnvironment,
    onStderr: recordChromeLog,
  })

  try {
    await waitForJson(
      `${baseUrl}api/health`,
      value => value?.ok === true && value?.database === 'ok',
      'Disposable Proxima API',
    )
    const cdp = await connectCdp(cdpPort, baseUrl)
    await initializePageCdp(cdp)

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
    assertSingleSemanticOwner(
      mismatchAx,
      node => node.name === 'Confirm password',
      /Passwords.*match/,
    )

    const repeatedMismatchFocusedBeforeError = await startAnnouncementTrace(cdp)
    await pressKey(cdp, 'Enter', 'Enter', 13, '\r')
    await waitForPage(
      cdp,
      `(window.__proximaA11yEvents || []).filter(event => event.type === 'alert').length === 1`,
      'Repeated mismatch announcement',
    )
    const repeatedMismatchTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      repeatedMismatchTrace,
      'password-confirmation',
      /Passwords.*match/,
      repeatedMismatchFocusedBeforeError === 'password-confirmation',
    )
    const repeatedMismatchAx = await accessibilitySummary(cdp)
    assertSingleSemanticOwner(
      repeatedMismatchAx,
      node => node.name === 'Confirm password',
      /Passwords.*match/,
    )
    await screenshot(cdp, 'auth-setup-mismatch-after.png')

    await setInput(cdp, 'password-confirmation', 'longenough1')
    const initialBrowseFailure = await failNextFolderBrowse(cdp)
    const initialBrowseFocusedBeforeError = await startAnnouncementTrace(cdp)
    await clickButton(cdp, 'Set password & enter')
    await initialBrowseFailure.intercepted
    await initialBrowseFailure.stop()
    await waitForPage(
      cdp,
      `document.querySelector('h1')?.textContent === 'Pick your working folder'
        && document.querySelector('button[name=selected-folder]')
        && document.querySelector('[role=alert]')?.textContent.includes('No readable folder')`,
      'Initial folder browser recovery',
    )
    assert.equal(await evaluate(cdp, `document.querySelectorAll('main').length`), 1)
    const initialBrowseTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      initialBrowseTrace,
      'selected-folder',
      /^No readable folder is available inside the allowed roots$/,
      initialBrowseFocusedBeforeError === 'selected-folder',
    )
    const initialBrowseAx = await accessibilitySummary(cdp)
    assertSingleSemanticOwner(
      initialBrowseAx,
      node => node.role === 'button' && node.name === 'Folder browser. Retry folders',
      /^No readable folder is available inside the allowed roots$/,
    )
    assert.equal(
      await evaluate(cdp, `document.activeElement?.getAttribute('name')`),
      'selected-folder',
    )
    assert.equal(
      await evaluate(
        cdp,
        `document.querySelector('button[name=selected-folder]').getAttribute('aria-invalid')`,
      ),
      'true',
    )
    await screenshot(cdp, 'onboarding-folder-load-error-after.png')
    await pressKey(cdp, 'Enter', 'Enter', 13, '\r')
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent
          === ${JSON.stringify(fixtureHome)}
        && !document.querySelector('[role=alert]')`,
      'Initial folder browser retry',
    )
    assert.equal(
      await evaluate(cdp, `document.activeElement?.getAttribute('name')`),
      'selected-folder',
    )

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
    assert.equal(folderAx.tabs.length, 0)
    assert(folderAx.buttons.some(node => node.name === 'Create new folder' && node.pressed === 'true'))
    assertSingleSemanticOwner(
      folderAx,
      node => node.name.includes('New folder name'),
      /cannot contain slashes/,
    )

    await activateButtonByKeyboard(
      cdp,
      'Create “bad/name” here',
      'Enter',
      'Enter',
      13,
      '\r',
    )
    await waitForPage(
      cdp,
      `(window.__proximaA11yEvents || []).filter(event => event.type === 'alert').length === 1`,
      'Repeated unsafe-folder Enter announcement',
    )
    const repeatedFolderEnterTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      repeatedFolderEnterTrace,
      'folder-name',
      /cannot contain slashes/,
    )
    await activateButtonByKeyboard(
      cdp,
      'Create “bad/name” here',
      ' ',
      'Space',
      32,
      ' ',
    )
    await waitForPage(
      cdp,
      `(window.__proximaA11yEvents || []).filter(event => event.type === 'alert').length === 1`,
      'Repeated unsafe-folder Space announcement',
    )
    const repeatedFolderSpaceTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      repeatedFolderSpaceTrace,
      'folder-name',
      /cannot contain slashes/,
    )
    const repeatedFolderKeyboardAx = await accessibilitySummary(cdp)
    assertSingleSemanticOwner(
      repeatedFolderKeyboardAx,
      node => node.name.includes('New folder name'),
      /cannot contain slashes/,
    )
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
      const browse = await fetch('/api/fs/dirs')
      const selected = await browse.json()
      const response = await fetch('/api/projects/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: ${JSON.stringify(path.join(fixtureHome, 'reserved-project'))},
          root_id: selected.root_id,
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

    await setInput(cdp, 'folder-name', 'valid-child')
    await setInput(cdp, 'project-display-name', '')
    await clickButton(cdp, 'disappearing-parent')
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent.endsWith('/disappearing-parent')`,
      'Selected disappearing create parent',
    )
    fs.rmdirSync(disappearingParent)
    await activateButtonByKeyboard(
      cdp,
      'Create “valid-child” here',
      'Enter',
      'Enter',
      13,
      '\r',
    )
    await waitForPage(
      cdp,
      `document.querySelector('[role=alert]')?.textContent.includes('parent directory does not exist')`,
      'Missing create parent error',
    )
    const missingCreateParentTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      missingCreateParentTrace,
      'selected-folder',
      /parent directory does not exist/,
    )
    const missingCreateParentAx = await accessibilitySummary(cdp)
    assertSingleSemanticOwner(
      missingCreateParentAx,
      node => node.role === 'button' && node.name.includes('Selected folder:'),
      /parent directory does not exist/,
    )
    await screenshot(cdp, 'onboarding-create-parent-error-after.png')
    await refreshSelectedFolder(cdp)
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent
          === ${JSON.stringify(fixtureHome)}
        && !document.querySelector('[role=alert]')`,
      'Missing create parent recovery',
    )

    await clickButton(cdp, 'permission-denied-parent')
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent.endsWith('/permission-denied-parent')`,
      'Selected permission-denied create parent',
    )
    fs.chmodSync(permissionDeniedParent, 0o000)
    await setInput(cdp, 'folder-name', 'permission-child')
    await activateButtonByKeyboard(
      cdp,
      'Create “permission-child” here',
      ' ',
      'Space',
      32,
      ' ',
    )
    await waitForPage(
      cdp,
      `document.querySelector('[role=alert]')?.textContent.includes('permission denied')`,
      'Permission-denied create parent error',
    )
    const permissionParentTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      permissionParentTrace,
      'selected-folder',
      /permission denied/,
    )
    const permissionParentAx = await accessibilitySummary(cdp)
    assertSingleSemanticOwner(
      permissionParentAx,
      node => node.role === 'button' && node.name.includes('Selected folder:'),
      /permission denied/,
    )
    await screenshot(cdp, 'onboarding-parent-permission-after.png')
    await refreshSelectedFolder(cdp)
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent
          === ${JSON.stringify(fixtureHome)}
        && !document.querySelector('[role=alert]')`,
      'Unreadable selected-parent ancestor recovery',
    )
    fs.chmodSync(permissionDeniedParent, 0o700)

    await clickButton(cdp, 'no-readable-parent')
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent.endsWith('/no-readable-parent')`,
      'Selected no-readable-ancestor fixture',
    )
    fs.chmodSync(noReadableParent, 0o000)
    fs.chmodSync(fixtureHome, 0o000)
    const noReadableFocusedBeforeError = await startAnnouncementTrace(cdp)
    await refreshSelectedFolder(cdp)
    await waitForPage(
      cdp,
      `document.querySelector('[role=alert]')?.textContent.includes('Selected folder root is not reachable')`,
      'No readable ancestor error',
    )
    const noReadableTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      noReadableTrace,
      'selected-folder',
      /^Selected folder root is not reachable$/,
      noReadableFocusedBeforeError === 'selected-folder',
    )
    const noReadableAx = await accessibilitySummary(cdp)
    assertSingleSemanticOwner(
      noReadableAx,
      node => node.role === 'button' && node.name.includes('Selected folder:'),
      /^Selected folder root is not reachable$/,
    )
    assert.equal(
      await evaluate(cdp, `document.querySelector('button[name=selected-folder] code')?.textContent`),
      noReadableParent,
    )
    fs.chmodSync(fixtureHome, 0o700)
    fs.chmodSync(noReadableParent, 0o700)
    await refreshSelectedFolder(cdp)
    await waitForPage(
      cdp,
      `!document.querySelector('[role=alert]')`,
      'No-readable-ancestor retry',
    )
    await clickButton(cdp, '↑ ..')
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent
          === ${JSON.stringify(fixtureHome)}`,
      'Returned to fixture root',
    )

    await clickButton(cdp, 'Link existing')
    await waitForPage(
      cdp,
      `document.querySelector('button[aria-pressed=true]')?.textContent.trim() === 'Link existing'`,
      'Link mode',
    )
    await clickButton(cdp, 'vanishing-folder')
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent.endsWith('/vanishing-folder')`,
      'Selected disposable folder',
    )
    fs.rmdirSync(vanishingFolder)
    const selectedPathFocusedBeforeError = await startAnnouncementTrace(cdp)
    await clickButton(cdp, 'Link “vanishing-folder”')
    await waitForPage(
      cdp,
      `document.querySelector('[role=alert]')?.textContent.includes('selected folder is not reachable')`,
      'Missing selected folder error',
    )
    const selectedPathTrace = await announcementTrace(cdp)
    assertSingleAnnouncement(
      selectedPathTrace,
      'selected-folder',
      /selected folder is not reachable/,
      selectedPathFocusedBeforeError === 'selected-folder',
    )
    const selectedPathAx = await accessibilitySummary(cdp)
    assertSingleSemanticOwner(
      selectedPathAx,
      node => node.role === 'button' && node.name.includes('Selected folder:'),
      /selected folder is not reachable/,
    )
    await screenshot(cdp, 'onboarding-path-error-after.png')

    await refreshSelectedFolder(cdp)
    await waitForPage(
      cdp,
      `document.querySelector('button[name=selected-folder] code')?.textContent
          === ${JSON.stringify(fixtureHome)}
        && !document.querySelector('[role=alert]')`,
      'Selected folder recovery',
    )
    assert.equal(await evaluate(
      cdp,
      `document.activeElement?.getAttribute('name')`,
    ), 'selected-folder')
    assert.equal(await evaluate(
      cdp,
      `document.querySelector('button[name=selected-folder]').getAttribute('aria-invalid')`,
    ), null)

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
    assertSingleSemanticOwner(
      loginAx,
      node => node.name === 'Password',
      /Incorrect password/,
    )
    await screenshot(cdp, 'auth-login-error-after.png')

    await setInput(cdp, 'password', 'longenough1')
    await clickButton(cdp, 'Log in')
    await waitForPage(cdp, `Boolean(document.querySelector('.app-shell'))`, 'Returning login success')

    const remoteBrowserOptions = {
      fixtureRoot,
      browserEnvironment,
      hostResolverRules,
      onChromeLog: recordChromeLog,
    }
    const workerFixture = await auditRemoteEntryInIsolatedBrowser(
      baseUrl,
      {
        origin: 'isolated loopback production worker fixture',
        proveServiceWorkerArtifact: true,
      },
      remoteBrowserOptions,
    )
    const tailnetFixture = await auditRemoteEntryInIsolatedBrowser(
      tailnetFixtureUrl,
      {
        origin: 'isolated Tailnet-host fixture',
        screenshotName: 'tailnet-unauthenticated-entry.png',
      },
      remoteBrowserOptions,
    )
    const tailscaleEntry = await auditRemoteEntryInIsolatedBrowser(
      privateTailscale.url,
      {
        origin: 'private Tailscale origin (redacted)',
        provenance: privateTailscale.provenance,
        assertAccessibilityContract: false,
        proveServiceWorkerArtifact: true,
      },
      remoteBrowserOptions,
    )
    const lighthouseResult = runLighthouse(baseUrl, browserEnvironment)
    const report = {
      viewport: { width: 1440, height: 1000 },
      runtime: { node: process.version },
      fixture: 'disposable production bundle and owner database',
      isolation: {
        environment: 'allowlisted',
        writableRoots: 'disposable fixture only',
        apiBackgroundWorker: 'disabled',
        remoteBrowserProfiles: 'isolated per origin',
        liveServiceWrites: 'disabled',
      },
      announcements: {
        setupMismatch: mismatchTrace,
        setupMismatchRepeat: repeatedMismatchTrace,
        unsafeFolder: folderTrace,
        unsafeFolderEnter: repeatedFolderEnterTrace,
        unsafeFolderSpace: repeatedFolderSpaceTrace,
        overlongDisplayName: displayTrace,
        derivedSlugCollision: collisionTrace,
        missingCreateParent: missingCreateParentTrace,
        permissionDeniedParent: permissionParentTrace,
        noReadableAncestor: noReadableTrace,
        selectedPath: selectedPathTrace,
        initialFolderBrowse: initialBrowseTrace,
        returningLogin: loginTrace,
      },
      accessibilityTrees: {
        setupMismatch: mismatchAx,
        setupMismatchRepeat: repeatedMismatchAx,
        unsafeFolder: folderAx,
        unsafeFolderKeyboard: repeatedFolderKeyboardAx,
        missingCreateParent: missingCreateParentAx,
        permissionDeniedParent: permissionParentAx,
        noReadableAncestor: noReadableAx,
        selectedPath: selectedPathAx,
        initialFolderBrowse: initialBrowseAx,
        returningLogin: loginAx,
      },
      themes: themeResults,
      workerFixture,
      tailnetFixture,
      tailscaleEntry,
      lighthouse: lighthouseResult,
    }
    writeEvidence(report)
    process.stdout.write(`accessibility audit: pass, Lighthouse ${lighthouseResult.score}\n`)
  } catch (error) {
    throw new Error(`${error.stack || error}\nAPI log:\n${serverLog}\nChrome log:\n${chromeLog}`)
  } finally {
    for (const directory of [fixtureHome, permissionDeniedParent, noReadableParent]) {
      try {
        fs.chmodSync(directory, 0o700)
      } catch {
        continue
      }
    }
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
