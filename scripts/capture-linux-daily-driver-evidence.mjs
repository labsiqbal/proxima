#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const chromePath = process.env.CHROME_PATH || '/usr/bin/google-chrome'
const baseUrl = String(process.argv[2] || '').replace(/\/$/, '')
const password = String(process.env.PROXIMA_EVIDENCE_PASSWORD || '')
const fixtureRoot = path.resolve(process.env.PROXIMA_BROWSER_FIXTURE_ROOT || '')
const outputPath = path.resolve(
  process.argv[3]
    || path.join(repoRoot, 'docs', 'evidence', 'linux-daily-driver', 'diagnostics-platform-support.png'),
)
const expectedEvidenceRoot = path.join(repoRoot, 'docs', 'evidence', 'linux-daily-driver')
const target = new URL(baseUrl)

assert(['127.0.0.1', 'localhost'].includes(target.hostname), 'Evidence target must be loopback')
assert(password.length >= 8, 'PROXIMA_EVIDENCE_PASSWORD must be provided')
assert(
  fixtureRoot.startsWith(`${path.resolve(os.tmpdir())}${path.sep}`),
  'Browser fixture root must be beneath the system temporary directory',
)
assert(
  outputPath === expectedEvidenceRoot || outputPath.startsWith(`${expectedEvidenceRoot}${path.sep}`),
  'Evidence output must stay beneath docs/evidence/linux-daily-driver',
)

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      server.close(() => {
        if (typeof address === 'object' && address?.port) resolve(address.port)
        else reject(new Error('Could not allocate Chrome DevTools port'))
      })
    })
  })
}

async function waitForJson(url, predicate, label) {
  let lastError
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      const response = await fetch(url)
      const value = await response.json()
      if (predicate(value)) return value
    } catch (error) {
      lastError = error
    }
    await sleep(50)
  }
  throw new Error(`${label} did not become ready: ${lastError || 'timeout'}`)
}

class CdpClient {
  constructor(socket) {
    this.socket = socket
    this.nextId = 0
    this.pending = new Map()
    this.listeners = new Map()
    socket.onmessage = event => {
      const message = JSON.parse(String(event.data))
      if (message.id) {
        const pending = this.pending.get(message.id)
        if (!pending) return
        this.pending.delete(message.id)
        if (message.error) pending.reject(new Error(message.error.message))
        else pending.resolve(message.result || {})
        return
      }
      for (const listener of this.listeners.get(message.method) || []) {
        listener(message.params || {})
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

  close() {
    this.socket.close()
  }
}

async function connect(port) {
  const pages = await waitForJson(
    `http://127.0.0.1:${port}/json`,
    value => Array.isArray(value) && value.some(page => page.type === 'page'),
    'Chrome DevTools',
  )
  const page = pages.find(candidate => candidate.url.startsWith(baseUrl))
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

async function waitFor(cdp, expression, label) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await evaluate(cdp, expression)) return
    await sleep(50)
  }
  throw new Error(`${label} timed out`)
}

async function clickText(cdp, text) {
  return evaluate(cdp, `(() => {
    const target = ${JSON.stringify(text)}
    const button = [...document.querySelectorAll('button')].find(candidate =>
      candidate.textContent?.trim() === target
      || candidate.getAttribute('aria-label')?.startsWith(target)
    )
    if (!button) return false
    button.click()
    return true
  })()`)
}

async function fillPasswordAndSubmit(cdp) {
  const filled = await evaluate(cdp, `(() => {
    const password = ${JSON.stringify(password)}
    const inputs = [...document.querySelectorAll('input[type=password]')]
    if (!inputs.length) return false
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    for (const input of inputs) {
      setter.call(input, password)
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
    }
    return true
  })()`)
  assert(filled, 'Password form was not available')
  await sleep(50)
  const submitted = await evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('button')].find(candidate =>
      ['Log in', 'Set password & enter'].includes(candidate.textContent?.trim())
    )
    if (!button) return false
    button.click()
    return true
  })()`)
  assert(submitted, 'Login submit button was not available')
}

async function driveEvidence(cdp) {
  const consoleErrors = []
  cdp.on('Runtime.exceptionThrown', params => {
    consoleErrors.push(params.exceptionDetails?.text || 'Runtime exception')
  })
  cdp.on('Log.entryAdded', params => {
    if (params.entry?.level === 'error') consoleErrors.push(params.entry.text)
  })
  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')
  await cdp.send('Log.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  })

  await waitFor(
    cdp,
    `Boolean(document.querySelector('button') || document.querySelector('input[type=password]'))`,
    'Initial application page',
  )
  const needsLogin = await evaluate(cdp, `Boolean(document.querySelector('input[type=password]'))`)
  if (needsLogin) {
    await fillPasswordAndSubmit(cdp)
  }
  await waitFor(
    cdp,
    `[...document.querySelectorAll('button')].some(button => button.getAttribute('aria-label') === 'Settings')`,
    'Authenticated application shell',
  )
  // Auth-gate probes can emit expected 401 network log noise before the session exists.
  // Clear them once the authenticated shell is visible so only post-auth errors fail the run.
  consoleErrors.length = 0

  for (const label of ['Skip for now', 'Skip tour']) {
    await clickText(cdp, label)
    await sleep(100)
  }
  const settingsOpened = await evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('button')].find(candidate =>
      candidate.getAttribute('aria-label') === 'Settings'
    )
    if (!button) return false
    button.click()
    return true
  })()`)
  assert(settingsOpened, 'Settings button was not available')
  await waitFor(cdp, `document.querySelector('[aria-label="Settings sections"]') !== null`, 'Settings')
  assert(await clickText(cdp, 'Diagnostics.'), 'Diagnostics section was not available')
  await waitFor(cdp, `document.querySelector('.platform-support-list') !== null`, 'Platform support panel')

  const assertions = await evaluate(cdp, `(async () => {
    const rows = [...document.querySelectorAll('.platform-support-row')].map(row => ({
      label: row.querySelector('strong')?.textContent?.trim(),
      tier: row.querySelector('.platform-support-tier')?.textContent?.trim(),
    }))
    const masterVisible = [...document.querySelectorAll('button')].some(button =>
      button.getAttribute('aria-label')?.startsWith('Master.')
    )
    const config = await fetch('/api/config').then(response => response.json())
    return {
      rows,
      masterVisible,
      server: config.platform_support?.server,
      safeSelfUpdate: config.features?.safe_self_update,
      targetText: document.querySelector('.platform-support-panel > p')?.textContent || '',
    }
  })()`)
  assert.deepEqual(assertions.rows, [
    { label: 'Linux', tier: 'Supported' },
    { label: 'macOS', tier: 'Experimental' },
    { label: 'Windows', tier: 'Experimental' },
  ])
  assert.equal(assertions.server?.key, 'linux')
  assert.equal(assertions.server?.tier, 'supported')
  assert.equal(assertions.masterVisible, true)
  assert.equal(assertions.safeSelfUpdate, false)
  assert.match(assertions.targetText, /Linux Mint/)
  assert.match(assertions.targetText, /CachyOS/)
  assert.match(assertions.targetText, /Tailscale/)
  assert.deepEqual(consoleErrors, [])

  const capture = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  })
  return {
    assertions,
    png: Buffer.from(capture.data, 'base64'),
    consoleErrors,
  }
}

async function main() {
  fs.mkdirSync(expectedEvidenceRoot, { recursive: true })
  fs.mkdirSync(fixtureRoot, { recursive: true })
  const profile = path.join(fixtureRoot, 'raw-cdp-profile')
  const port = await freePort()
  const stderr = []
  const chrome = spawn(chromePath, [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--no-proxy-server',
    '--disable-background-networking',
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    '--window-size=1440,1000',
    baseUrl,
  ], {
    env: {
      PATH: process.env.PATH,
      HOME: path.join(fixtureRoot, 'chrome-home'),
      LANG: process.env.LANG || 'C.UTF-8',
    },
    stdio: ['ignore', 'ignore', 'pipe'],
  })
  chrome.stderr.on('data', chunk => stderr.push(String(chunk)))
  let cdp
  try {
    cdp = await connect(port)
    const evidence = await driveEvidence(cdp)
    fs.writeFileSync(outputPath, evidence.png, { mode: 0o644 })
    const bytes = fs.readFileSync(outputPath)
    assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
    const width = bytes.readUInt32BE(16)
    const height = bytes.readUInt32BE(20)
    assert(width > 0 && height > 0, 'PNG dimensions must be nonzero')
    const stat = fs.statSync(outputPath)
    assert(stat.isFile() && stat.size === bytes.length && stat.size > 8)
    assert.equal(path.resolve(outputPath), outputPath)
    console.log(JSON.stringify({
      ok: true,
      fixture: fixtureRoot,
      output: outputPath,
      bytes: stat.size,
      width,
      height,
      platformAssertions: evidence.assertions,
      consoleErrors: evidence.consoleErrors,
    }, null, 2))
  } catch (error) {
    if (stderr.length) process.stderr.write(stderr.join(''))
    throw error
  } finally {
    cdp?.close()
    chrome.kill('SIGTERM')
    await Promise.race([
      new Promise(resolve => chrome.once('exit', resolve)),
      sleep(5000).then(() => chrome.kill('SIGKILL')),
    ])
  }
}

await main()
