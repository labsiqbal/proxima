#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
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
const evidenceDir = path.resolve(
  process.env.PROXIMA_A11Y_EVIDENCE_DIR
    || path.join(repoRoot, 'docs', 'evidence', 'auth-onboarding-accessibility'),
)
const themes = ['light', 'dark', 'ocean', 'violet', 'sunset', 'forest']

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

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
  await waitForPage(
    cdp,
    `document.querySelector('h1')?.textContent === ${JSON.stringify(heading)}`,
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

async function auditRemoteEntry(cdp, url, screenshotName) {
  const requests = []
  const listener = request => requests.push({ method: request.request.method, url: request.request.url })
  cdp.on('Network.requestWillBeSent', listener)
  await cdp.send('Network.clearBrowserCookies')
  await navigate(cdp, url, 'Welcome back')
  const state = await evaluate(cdp, `(() => ({
    title: document.title,
    mainCount: document.querySelectorAll('main').length,
    passwordCount: document.querySelectorAll('input[autocomplete=current-password]').length,
    owner: document.querySelector('input[name=username]')?.value,
  }))()`)
  assert.equal(state.title, 'Proxima')
  assert.equal(state.mainCount, 1)
  assert.equal(state.passwordCount, 1)
  assert.equal(state.owner, 'owner')
  const forbidden = requests.filter(request => (
    request.method !== 'GET'
    && !request.url.endsWith('/auth/resume')
  ))
  assert.deepEqual(forbidden, [])
  await screenshot(cdp, screenshotName)
  return {
    url,
    mainCount: state.mainCount,
    ownerMetadata: state.owner,
    requests: requests.map(request => `${request.method} ${new URL(request.url).pathname}`),
  }
}

function runLighthouse(baseUrl) {
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
    env: { ...process.env, CHROME_PATH: chromePath },
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
  const remote = report.tailscaleEntry
    ? `pass - ${report.tailscaleEntry.url}`
    : 'not configured - set PROXIMA_A11Y_REMOTE_BASE to retain a private Tailnet pass'
  const markdown = `# Auth and onboarding accessibility evidence

This pass uses the production web bundle, a disposable owner database, and headless
Chrome at 1440 x 1000. It does not read or alter live Proxima data.

| Check | Result |
| --- | --- |
| First-run mismatch focus and single announcement | pass |
| Unsafe folder focus and single announcement | pass |
| Overlong display-name field routing | pass |
| Pressed-button Tab and Space behavior | pass |
| Returning login failure and success | pass |
| Accessibility trees and one main landmark | pass |
| Six-theme WCAG AA text contrast | pass |
| Lighthouse accessibility | ${report.lighthouse.score} |
| Isolated Tailnet-host unauthenticated entry | pass |
| Private Tailscale unauthenticated entry | ${remote} |

## Before and after

| Flow | Before | After |
| --- | --- | --- |
| Password gate | [tour capture](../../screenshots/first-run-password.png) | [setup mismatch](auth-setup-mismatch-after.png), [returning login](auth-login-error-after.png) |
| Folder onboarding | [link](../../screenshots/onboarding-link-folder.png), [create](../../screenshots/onboarding-create-folder.png) | [unsafe folder](onboarding-validation-after.png) |
| Remote entry | - | [isolated Tailnet-host login](tailnet-unauthenticated-entry.png) |

Machine-readable details are in [report.json](report.json), with the full
[Lighthouse report](lighthouse.json).
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
  fs.mkdirSync(fixtureHome, { recursive: true })
  fs.mkdirSync(workspaceRoot, { recursive: true })

  let serverLog = ''
  let chromeLog = ''
  const api = spawn(apiCommand, apiArguments, {
    cwd: repoRoot,
    env: {
      ...process.env,
      HOME: fixtureHome,
      XDG_CONFIG_HOME: path.join(fixtureRoot, 'config'),
      XDG_DATA_HOME: dataRoot,
      PROXIMA_REPO_ROOT: repoRoot,
      PROXIMA_DB_PATH: path.join(dataRoot, 'proxima.db'),
      PROXIMA_WORKSPACE_ROOT: workspaceRoot,
      PROXIMA_PROJECTCTL_COMMAND: '/usr/bin/true',
      PROXIMA_WEB_DIST: webDist,
      PROXIMA_HOST: '127.0.0.1',
      PROXIMA_PORT: String(apiPort),
      PROXIMA_SINGLE_USER: '1',
      PROXIMA_SINGLE_USER_NAME: 'accessibility-owner',
      PROXIMA_LINK_ROOTS: fixtureHome,
      PROXIMA_REFRESH_CREDENTIALS: '0',
      PROXIMA_UPDATE_CHECK: '0',
    },
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
    '--host-resolver-rules=MAP proxima.tailnet.test 127.0.0.1',
    `--user-data-dir=${chromeProfile}`,
    `--remote-debugging-port=${cdpPort}`,
    '--window-size=1440,1000',
    baseUrl,
  ], { stdio: ['ignore', 'ignore', 'pipe'] })
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
      'tailnet-unauthenticated-entry.png',
    )
    const remoteBase = process.env.PROXIMA_A11Y_REMOTE_BASE?.trim()
    const tailscaleEntry = remoteBase
      ? await auditRemoteEntry(
        cdp,
        new URL('/', remoteBase).toString(),
        'tailscale-unauthenticated-entry.png',
      )
      : null
    const lighthouseResult = runLighthouse(baseUrl)
    const report = {
      viewport: { width: 1440, height: 1000 },
      fixture: 'disposable production bundle and owner database',
      announcements: {
        setupMismatch: mismatchTrace,
        unsafeFolder: folderTrace,
        overlongDisplayName: displayTrace,
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
