#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import { createRequire } from 'node:module'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const webRoot = path.join(repoRoot, 'apps', 'web')
const apiRoot = path.join(repoRoot, 'apps', 'api')
const serve = path.join(apiRoot, 'scripts', 'serve.py')
const python = path.join(apiRoot, '.venv', 'bin', 'python')
const webDist = path.join(webRoot, 'dist')
const chromePath = process.env.CHROME_PATH || '/usr/bin/google-chrome'
const sqlitePath = '/usr/bin/sqlite3'
const require = createRequire(path.join(webRoot, 'package.json'))
const WebSocket = require('ws')

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

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

function allowlistedEnvironment(overrides = {}) {
  const environment = {}
  for (const name of ['PATH', 'LANG', 'LC_ALL', 'TZ']) {
    if (process.env[name]) environment[name] = process.env[name]
  }
  return { ...environment, ...overrides }
}

function requestJson(port, method, requestPath, body = null) {
  return new Promise((resolve, reject) => {
    const encoded = body == null ? null : JSON.stringify(body)
    const request = http.request({
      hostname: '127.0.0.1',
      port,
      method,
      path: requestPath,
      headers: encoded
        ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(encoded) }
        : {},
    }, response => {
      let text = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { text += chunk })
      response.on('end', () => {
        if ((response.statusCode || 500) >= 400) {
          reject(new Error(`${method} ${requestPath} failed (${response.statusCode}): ${text}`))
          return
        }
        try {
          resolve(text ? JSON.parse(text) : {})
        } catch (error) {
          reject(error)
        }
      })
    })
    request.on('error', reject)
    if (encoded) request.write(encoded)
    request.end()
  })
}

async function waitForApi(port) {
  let lastError
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      return await requestJson(port, 'GET', '/api/setup/status')
    } catch (error) {
      lastError = error
      await sleep(100)
    }
  }
  throw new Error(`Disposable API did not become ready: ${lastError}`)
}

class CdpClient {
  constructor(socket) {
    this.socket = socket
    this.nextId = 0
    this.pending = new Map()
    socket.onmessage = event => {
      const message = JSON.parse(event.data)
      if (!message.id || !this.pending.has(message.id)) return
      const pending = this.pending.get(message.id)
      this.pending.delete(message.id)
      clearTimeout(pending.timeout)
      if (message.error) pending.reject(new Error(JSON.stringify(message.error)))
      else pending.resolve(message.result)
    }
    const close = () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timeout)
        pending.reject(new Error('Chrome DevTools connection closed'))
      }
      this.pending.clear()
    }
    socket.onerror = close
    socket.onclose = close
  }

  send(method, params = {}, timeoutMilliseconds = 10000) {
    return new Promise((resolve, reject) => {
      const id = ++this.nextId
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`Timed out sending ${method}`))
      }, timeoutMilliseconds)
      this.pending.set(id, { resolve, reject, timeout })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  close() {
    this.socket.close()
  }
}

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url} returned ${response.status}`)
  return response.json()
}

async function connectCdp(port, expectedUrl) {
  let page
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const pages = await fetchJson(`http://127.0.0.1:${port}/json`)
      page = pages.find(candidate => candidate.url === expectedUrl)
        || pages.find(candidate => candidate.type === 'page')
      if (page?.webSocketDebuggerUrl) break
    } catch {
      // Chrome can take a moment to publish its first target.
    }
    await sleep(100)
  }
  assert(page?.webSocketDebuggerUrl, 'Chrome page target was unavailable')
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  const cdp = new CdpClient(socket)
  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')
  await cdp.send('Accessibility.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  })
  return cdp
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

async function setInput(cdp, selector, value) {
  const changed = await evaluate(cdp, `(() => {
    const input = document.querySelector(${JSON.stringify(selector)})
    if (!(input instanceof HTMLInputElement) && !(input instanceof HTMLTextAreaElement)) return false
    const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, ${JSON.stringify(value)})
    input.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  })()`)
  assert(changed, `Missing input ${selector}`)
}

async function clickByText(cdp, label, selector = 'button') {
  const clicked = await evaluate(cdp, `(() => {
    const target = [...document.querySelectorAll(${JSON.stringify(selector)})]
      .find(candidate => candidate.textContent.trim() === ${JSON.stringify(label)})
    if (!(target instanceof HTMLElement)) return false
    target.click()
    return true
  })()`)
  assert(clicked, `Missing ${selector} with text ${label}`)
}

async function switchProject(cdp, name) {
  await evaluate(cdp, `(() => {
    const trigger = document.querySelector('button[aria-label^="Active project:"]')
    if (!(trigger instanceof HTMLButtonElement)) return false
    trigger.click()
    return true
  })()`)
  await waitForPage(cdp, `Boolean(document.querySelector('[role=listbox][aria-label="Projects"]'))`, 'project list')
  const picked = await evaluate(cdp, `(() => {
    const option = [...document.querySelectorAll('[role=option]')]
      .find(candidate => candidate.textContent.includes(${JSON.stringify(name)}))
    if (!(option instanceof HTMLElement)) return false
    option.click()
    return true
  })()`)
  assert(picked, `Missing project ${name}`)
  await waitForPage(
    cdp,
    `document.querySelector('button[aria-label^="Active project:"]')?.getAttribute('aria-label') === ${JSON.stringify(`Active project: ${name}`)}`,
    `${name} project`,
  )
}

async function pressKey(cdp, key, code, keyCode, modifiers = 0) {
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key,
    code,
    modifiers,
    windowsVirtualKeyCode: keyCode,
    nativeVirtualKeyCode: keyCode,
  })
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key,
    code,
    modifiers,
    windowsVirtualKeyCode: keyCode,
    nativeVirtualKeyCode: keyCode,
  })
}

async function captureScreenshot(cdp, outputPath) {
  const capture = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  })
  fs.writeFileSync(outputPath, Buffer.from(capture.data, 'base64'))
}

function seedArchiveRecord(databasePath, projectSlug, sessionId) {
  const sql = `
    INSERT INTO artifact_records(
      project_id, slug, name, type, path, size, status, version,
      session_id, file_missing, produced_at
    )
    SELECT id, 'launch-review-v1', 'Launch review', 'doc', 'reports/review.md',
      74, 'draft', 1, ${Number(sessionId)}, 0, CURRENT_TIMESTAMP
    FROM projects WHERE slug = '${projectSlug.replaceAll("'", "''")}';
  `
  const result = spawnSync(sqlitePath, [databasePath, sql], { encoding: 'utf8' })
  assert.equal(result.status, 0, `Could not seed artifact record: ${result.stderr}`)
}

async function main() {
  for (const required of [serve, python, webDist, chromePath, sqlitePath]) {
    assert(fs.existsSync(required), `Required browser-test dependency is missing: ${required}`)
  }

  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'proxima-artifact-feedback-'))
  const dataRoot = path.join(fixtureRoot, 'data')
  const workspaceRoot = path.join(dataRoot, 'workspace')
  const databasePath = path.join(dataRoot, 'proxima.db')
  const chromeProfile = path.join(fixtureRoot, 'chrome')
  const evidenceDir = process.env.PROXIMA_ARTIFACT_FEEDBACK_EVIDENCE_DIR
    ? path.resolve(process.env.PROXIMA_ARTIFACT_FEEDBACK_EVIDENCE_DIR)
    : path.join(fixtureRoot, 'evidence')
  for (const directory of [
    dataRoot,
    workspaceRoot,
    chromeProfile,
    evidenceDir,
    path.join(dataRoot, 'hermes-profiles'),
    path.join(dataRoot, 'source-hermes'),
    path.join(fixtureRoot, 'cache'),
    path.join(fixtureRoot, 'config'),
  ]) {
    fs.mkdirSync(directory, { recursive: true })
  }

  const apiPort = await freePort()
  const cdpPort = await freePort()
  const baseUrl = `http://127.0.0.1:${apiPort}/`
  const apiEnvironment = allowlistedEnvironment({
    PROXIMA_REPO_ROOT: repoRoot,
    PROXIMA_DB_PATH: databasePath,
    PROXIMA_WORKSPACE_ROOT: workspaceRoot,
    PROXIMA_HERMES_PROFILES_ROOT: path.join(dataRoot, 'hermes-profiles'),
    PROXIMA_SOURCE_HERMES_HOME: path.join(dataRoot, 'source-hermes'),
    PROXIMA_HERMES_BIN: '/usr/bin/false',
    PROXIMA_PROJECTCTL_COMMAND: '/usr/bin/true',
    PROXIMA_WEB_DIST: webDist,
    PROXIMA_HOST: '127.0.0.1',
    PROXIMA_PORT: String(apiPort),
    PROXIMA_SINGLE_USER: '1',
    PROXIMA_SINGLE_USER_NAME: 'artifact-owner',
    PROXIMA_LINK_ROOTS: fixtureRoot,
    PROXIMA_START_WORKER: '0',
    PROXIMA_REFRESH_CREDENTIALS: '0',
    PROXIMA_UPDATE_CHECK: '0',
    PROXIMA_MANAGE_OS_ACL: '0',
    PROXIMA_CLAUDE_LIVE_HOME: '0',
    PROXIMA_PREVIEW_BIND: 'off',
    PROXIMA_GRAPH_SEMANTIC_EGRESS: '0',
    PROXIMA_FEATURE_MASTER_ORCHESTRATOR: '0',
    PROXIMA_FEATURE_SAFE_SELF_UPDATE: '0',
  })
  let apiLog = ''
  let chromeLog = ''
  const api = spawn(python, [serve], {
    cwd: repoRoot,
    env: apiEnvironment,
    stdio: ['ignore', 'ignore', 'pipe'],
  })
  api.stderr.on('data', chunk => { apiLog = `${apiLog}${chunk}`.slice(-12000) })
  let chrome = null
  let cdp = null

  try {
    await waitForApi(apiPort)
    await requestJson(apiPort, 'POST', '/auth/auto')
    const projects = await requestJson(apiPort, 'GET', '/api/projects')
    const producerProject = projects.projects[0]
    assert.equal(producerProject.slug, 'artifact-owner')
    await requestJson(apiPort, 'PUT', `/api/projects/${producerProject.slug}/file?path=reports/review.md`, {
      content: '# Launch review\n\nPlease check the hierarchy and final call to action.',
    })
    const producer = await requestJson(apiPort, 'POST', '/api/sessions', {
      title: 'Producing chat',
      project_slug: producerProject.slug,
      profile_id: 1,
    })
    await requestJson(apiPort, 'POST', `/api/sessions/${producer.id}/messages`, {
      role: 'user',
      content: 'Create the launch review document.',
    })
    await requestJson(apiPort, 'POST', '/api/projects', {
      slug: 'client-beta',
      name: 'Client Beta',
    })
    seedArchiveRecord(databasePath, producerProject.slug, producer.id)

    chrome = spawn(chromePath, [
      '--headless=new',
      '--disable-gpu',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--no-proxy-server',
      '--disable-background-networking',
      `--user-data-dir=${chromeProfile}`,
      `--remote-debugging-port=${cdpPort}`,
      '--window-size=1440,1000',
      baseUrl,
    ], {
      env: allowlistedEnvironment({
        XDG_CACHE_HOME: path.join(fixtureRoot, 'cache'),
        XDG_CONFIG_HOME: path.join(fixtureRoot, 'config'),
        XDG_DATA_HOME: dataRoot,
      }),
      stdio: ['ignore', 'ignore', 'pipe'],
    })
    chrome.stderr.on('data', chunk => { chromeLog = `${chromeLog}${chunk}`.slice(-12000) })
    cdp = await connectCdp(cdpPort, baseUrl)

    await waitForPage(cdp, `document.querySelector('h1')?.textContent === 'Set a password'`, 'password setup')
    await setInput(cdp, 'input[name=password]', 'correct horse battery')
    await setInput(cdp, 'input[name="password-confirmation"]', 'correct horse battery')
    await clickByText(cdp, 'Set password & enter')
    await waitForPage(cdp, `document.querySelector('h1')?.textContent === 'Pick your working folder'`, 'onboarding')
    await clickByText(cdp, 'Skip for now')
    await waitForPage(cdp, `Boolean(document.querySelector('nav[aria-label="Navigation"]'))`, 'app shell')
    const tourSkipped = await evaluate(cdp, `(() => {
      const button = [...document.querySelectorAll('button')]
        .find(candidate => candidate.textContent.trim() === 'Skip tour')
      if (!button) return false
      button.click()
      return true
    })()`)
    if (tourSkipped) await sleep(100)

    await switchProject(cdp, 'artifact-owner')
    await clickByText(cdp, 'Producing chat')
    await waitForPage(cdp, `document.querySelector('.code-header strong')?.textContent === 'Producing chat'`, 'producer chat')
    await setInput(cdp, '.composer textarea', 'Producer unsent draft')

    await switchProject(cdp, 'Client Beta')
    await waitForPage(cdp, `document.querySelector('.code-header strong')?.textContent === 'New chat'`, 'client new chat')
    assert.equal(await evaluate(cdp, `document.querySelector('.composer textarea')?.value`), '')
    await setInput(cdp, '.composer textarea', 'Client private draft')
    await clickByText(cdp, 'Archive')
    await waitForPage(cdp, `document.querySelector('.archive-row')?.textContent.includes('Launch review')`, 'archive record')
    const openedRow = await evaluate(cdp, `(() => {
      const row = [...document.querySelectorAll('.archive-row')]
        .find(candidate => candidate.textContent.includes('Launch review'))
      if (!(row instanceof HTMLButtonElement)) return false
      row.click()
      return true
    })()`)
    assert(openedRow, 'Launch review archive row was unavailable')
    await waitForPage(cdp, `Boolean(document.querySelector('.archive-exp-foot'))`, 'expanded archive row')
    const openRecord = await evaluate(cdp, `(() => {
      const button = [...document.querySelectorAll('.archive-exp-foot button')]
        .find(candidate => candidate.textContent.trim() === 'Open')
      if (!(button instanceof HTMLButtonElement)) return false
      button.focus()
      button.click()
      return true
    })()`)
    assert(openRecord, 'Artifact viewer trigger was unavailable')

    await waitForPage(cdp, `Boolean(document.querySelector('[role=dialog][aria-modal=true].av-overlay'))`, 'artifact review dialog')
    const dialogSummary = await evaluate(cdp, `(() => {
      const dialog = document.querySelector('[role=dialog][aria-modal=true].av-overlay')
      return {
        name: dialog?.querySelector('h2')?.textContent,
        focused: document.activeElement?.getAttribute('aria-label'),
      }
    })()`)
    assert.deepEqual(dialogSummary, {
      name: 'Artifact review: Launch review',
      focused: 'Close artifact review',
    })
    await captureScreenshot(cdp, path.join(evidenceDir, 'before-artifact-review.png'))
    await evaluate(cdp, `document.querySelector('[role=dialog] button')?.focus()`)
    await pressKey(cdp, 'Tab', 'Tab', 9, 8)
    assert.equal(
      await evaluate(cdp, `document.activeElement?.closest('label')?.textContent.trim().startsWith('General feedback')`),
      true,
      'Shift+Tab did not wrap to the final dialog control',
    )
    await pressKey(cdp, 'Escape', 'Escape', 27)
    await waitForPage(cdp, `!document.querySelector('.av-overlay')`, 'ordinary dialog close')
    await waitForPage(
      cdp,
      `document.activeElement?.textContent.trim() === 'Open'`,
      'artifact trigger focus restoration',
    )

    await evaluate(cdp, `document.activeElement.click()`)
    await waitForPage(cdp, `Boolean(document.querySelector('.av-overlay'))`, 'reopened artifact review')
    await setInput(cdp, '.av-general-note textarea', 'Tighten the final call to action.')
    await clickByText(cdp, 'Add feedback to chat')
    await waitForPage(cdp, `!document.querySelector('.av-overlay')`, 'successful feedback handoff')
    await waitForPage(
      cdp,
      `document.querySelector('[role=dialog] h3')?.textContent === 'This chat already has an unsent draft'`,
      'draft conflict dialog',
    )
    const conflictSummary = await evaluate(cdp, `(() => ({
      project: document.querySelector('button[aria-label^="Active project:"]')?.getAttribute('aria-label'),
      chat: document.querySelector('.code-header strong')?.textContent,
      draft: document.querySelector('.composer textarea')?.value,
      conflict: document.querySelector('[role=dialog]')?.textContent,
    }))()`)
    assert.equal(conflictSummary.project, 'Active project: artifact-owner')
    assert.equal(conflictSummary.chat, 'Producing chat')
    assert.equal(conflictSummary.draft, 'Producer unsent draft')
    assert.match(conflictSummary.conflict, /Append feedback/)
    await clickByText(cdp, 'Append feedback')
    await waitForPage(
      cdp,
      `document.querySelector('.composer textarea')?.value.includes('Review feedback for [Launch review](reports/review.md):')`,
      'editable feedback draft',
    )
    await waitForPage(
      cdp,
      `document.activeElement === document.querySelector('.composer textarea')`,
      'composer focus restoration',
    )
    await captureScreenshot(cdp, path.join(evidenceDir, 'after-producing-chat-draft.png'))

    await switchProject(cdp, 'Client Beta')
    assert.equal(
      await evaluate(cdp, `document.querySelector('.composer textarea')?.value`),
      'Client private draft',
      'Cross-project handoff lost the source chat draft',
    )

    process.stdout.write(`Artifact feedback real-browser verification passed. Evidence: ${evidenceDir}\n`)
  } catch (error) {
    throw new Error(`${error.stack || error}\nAPI log:\n${apiLog}\nChrome log:\n${chromeLog}`)
  } finally {
    cdp?.close()
    chrome?.kill('SIGTERM')
    api.kill('SIGTERM')
  }
}

main().catch(error => {
  process.stderr.write(`${error.stack || error}\n`)
  process.exitCode = 1
})
