import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRunner } from './AppRunner'
import { appExitSummary, appStart, appStatus, appStop, detectApps, getPublicConfig, previewAuth } from '../../api/files'
import { confirmDialog } from '../ui/Dialog'

vi.mock('../../api/files', () => ({
  appExitSummary: vi.fn(),
  appStart: vi.fn(),
  appStatus: vi.fn(),
  appStop: vi.fn(),
  appViewUrl: vi.fn(() => '/api/appview/demo/'),
  detectApps: vi.fn(),
  getPublicConfig: vi.fn(),
  previewAuth: vi.fn(),
}))
vi.mock('../ui/Dialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

describe('AppRunner collision feedback', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    // A pin persists per slug, so leaking one between cases silently changes
    // what the next render sends.
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(getPublicConfig).mockResolvedValue({ apps_domain: null })
    vi.mocked(previewAuth).mockResolvedValue({ ok: true })
    vi.mocked(detectApps).mockResolvedValue({ apps: [] })
    vi.mocked(appStatus).mockResolvedValue({ state: 'stopped', running: false, ready: false })
    vi.mocked(appExitSummary).mockReturnValue({
      tone: 'fail',
      title: 'Command failed (exit 1)',
      hint: 'Check the log below.',
    })
  })

  it('shows a port collision instead of presenting a foreign preview', async () => {
    const user = userEvent.setup()
    vi.mocked(appStart).mockRejectedValue(new Error('Port 5180 is already in use by another process. Proxima did not open, proxy, or stop it.'))
    vi.mocked(appStatus).mockResolvedValue({
      state: 'port_conflict',
      running: false,
      ready: false,
      requested_port: 5180,
      command: 'npm run dev',
      log: ['address already in use'],
      message: 'Port 5180 belongs to another process. Proxima did not open, proxy, or stop it.',
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /run/i }))

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Port 5180 is already in use' })).toBeInTheDocument())
    expect(screen.getByText(/did not open, proxy, or stop/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View logs' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change port' })).toBeInTheDocument()
    expect(screen.queryByTitle('App preview')).not.toBeInTheDocument()
    expect(appStart).toHaveBeenCalledWith('token', 'demo', 'npm run dev', 5180, '')

    await user.click(screen.getByRole('button', { name: 'View logs' }))
    expect(screen.getByText('address already in use')).toBeInTheDocument()
  })

  it('replaces the infinite spinner with a prolonged-start warning and controls', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'starting',
      running: true,
      ready: false,
      requested_port: 5180,
      command: 'npm run dev',
      log: ['building routes'],
      prolonged_start: true,
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByRole('heading', { name: 'Still waiting for a preview server' })).toBeInTheDocument()
    expect(screen.getByText(/taking longer than expected/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View logs' })).toBeInTheDocument()
    expect(screen.queryByTitle('App preview')).not.toBeInTheDocument()
  })

  it('restores the requested candidate when polling a prior conflict', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'port_conflict',
      running: false,
      ready: false,
      requested_port: 35180,
      command: 'npm run dev',
      log: [],
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByRole('spinbutton')).toHaveValue(35180)
    expect(screen.getByRole('button', { name: 'View logs' })).toBeInTheDocument()
  })

  it('explains unverified ownership without exposing the candidate listener', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ownership_unknown',
      running: true,
      ready: false,
      requested_port: 5180,
      command: 'npm run dev',
      log: [],
      message: 'A server answered on this port and Proxima cannot verify who owns it, so it will not proxy it.',
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByRole('heading', { name: 'Preview ownership could not be verified' })).toBeInTheDocument()
    expect(screen.getByText(/will not proxy it/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View logs' })).toBeInTheDocument()
    expect(screen.queryByTitle('App preview')).not.toBeInTheDocument()
  })

  it('stops the managed command before returning to port editing', async () => {
    const user = userEvent.setup()
    vi.mocked(appStatus).mockResolvedValue({
      state: 'port_conflict',
      running: false,
      ready: false,
      requested_port: 5180,
      command: 'npm run dev',
      log: [],
    })
    vi.mocked(appStop).mockResolvedValue({ ok: true })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Change port' }))

    await waitFor(() => expect(appStop).toHaveBeenCalledWith('token', 'demo'))
    expect(screen.getByRole('spinbutton')).toHaveFocus()
  })

  it('keeps ownership_unknown after Change port when Stop returns 409', async () => {
    const user = userEvent.setup()
    let currentStatus: Awaited<ReturnType<typeof appStatus>> = {
      state: 'port_conflict',
      running: false,
      ready: false,
      requested_port: 5180,
      command: 'npm run dev',
      log: ['address already in use'],
      message: 'Port 5180 belongs to another process. Proxima did not open, proxy, or stop it.',
    }
    vi.mocked(appStatus).mockImplementation(async () => currentStatus)
    vi.mocked(appStop).mockImplementation(async () => {
      currentStatus = {
        state: 'ownership_unknown',
        running: true,
        ready: false,
        requested_port: 5180,
        command: 'npm run dev',
        log: ['supervisor still live'],
        message: 'A server answered on this port and Proxima cannot verify who owns it, so it will not proxy it.',
      }
      throw new Error('Authenticated stop could not finish. The prior preview scope remains unresolved.')
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByRole('heading', { name: 'Port 5180 is already in use' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Change port' }))

    await waitFor(() => expect(appStop).toHaveBeenCalledWith('token', 'demo'))
    expect(await screen.findByRole('heading', { name: 'Preview ownership could not be verified' })).toBeInTheDocument()
    expect(screen.getByText(/cannot verify who owns it/i)).toBeInTheDocument()
    expect(screen.getByText(/will not proxy it/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /run/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'App stopped' })).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
  })

  it('keeps ready logs open across Reload and shows the buffer after Stop', async () => {
    const user = userEvent.setup()
    vi.mocked(appStatus)
      .mockResolvedValueOnce({
        state: 'ready',
        running: true,
        ready: true,
        requested_port: 5180,
        port: 4321,
        command: 'npm run dev',
        log: ['astro ready on 5180'],
      })
      .mockResolvedValue({
        state: 'stopped',
        running: false,
        ready: false,
        requested_port: 5180,
        command: 'npm run dev',
        log: ['astro ready on 5180', 'server stopped'],
      })
    vi.mocked(appStop).mockResolvedValue({ ok: true })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Logs' }))
    expect(screen.getByRole('region', { name: 'Command logs' })).toHaveTextContent('astro ready on 5180')

    await user.click(screen.getByRole('button', { name: 'Reload' }))
    expect(screen.getByRole('region', { name: 'Command logs' })).toHaveTextContent('astro ready on 5180')

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await screen.findByRole('heading', { name: 'App stopped' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('server stopped')
    expect(screen.getByRole('button', { name: 'Hide logs' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change port' })).toBeInTheDocument()
    // A clean stop leaves the box on auto: the port was Proxima's choice, not the
    // owner's, so Retry should be free to take a fresh one. Only a conflict or
    // unverified ownership puts a number back in the box to edit.
    expect(screen.getByRole('spinbutton')).toHaveValue(null)
  })

  it('uses the capability relay from a localhost origin', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ready',
      running: true,
      ready: true,
      requested_port: 5180,
      port: 5180,
      preview_port: 43123,
      command: 'npm run dev',
      log: [],
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    const frame = await screen.findByTitle('App preview')
    expect(frame).toHaveAttribute('src', expect.stringContaining('http://localhost:43123/'))
    expect(frame).not.toHaveAttribute('src', expect.stringContaining('127.0.0.1:5180'))
  })

  it('falls back to the same-origin appview proxy on an https origin', async () => {
    // Tailscale Serve fronts the app with TLS, but relays are plain HTTP: an
    // https iframe to the relay port fails its handshake and an http one is
    // mixed content. The same-origin proxy rides the page's own TLS instead.
    vi.stubGlobal('location', {
      hostname: 'linc.example.ts.net',
      protocol: 'https:',
    })
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ready',
      running: true,
      ready: true,
      requested_port: 5180,
      port: 5180,
      preview_port: 43123,
      command: 'npm run dev',
      log: [],
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    const frame = await screen.findByTitle('App preview')
    expect(frame).toHaveAttribute('src', expect.stringContaining('/api/appview/demo/'))
    expect(frame).not.toHaveAttribute('src', expect.stringContaining(':43123'))
  })

  it('uses the capability relay from a Tailscale origin', async () => {
    vi.stubGlobal('location', {
      hostname: '100.101.102.103',
      protocol: 'http:',
    })
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ready',
      running: true,
      ready: true,
      requested_port: 5180,
      port: 5180,
      preview_port: 43123,
      command: 'npm run dev',
      log: [],
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    const frame = await screen.findByTitle('App preview')
    expect(frame).toHaveAttribute(
      'src',
      expect.stringContaining('http://100.101.102.103:43123/'),
    )
  })

  it('keeps the Logs toggle usable after an exit with no output', async () => {
    const user = userEvent.setup()
    vi.mocked(appStatus).mockResolvedValue({
      state: 'exited',
      running: false,
      ready: false,
      requested_port: 5180,
      command: 'npm run build',
      log: [],
      exited: true,
      exit_code: 1,
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    await screen.findByText('Command failed (exit 1)')
    await user.click(screen.getByRole('button', { name: 'View logs' }))
    expect(await screen.findByText('No command logs yet.')).toBeInTheDocument()
  })
})

describe('AppRunner owner-power acknowledgement', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(confirmDialog).mockResolvedValue(true)
    vi.mocked(getPublicConfig).mockResolvedValue({ apps_domain: null })
    vi.mocked(previewAuth).mockResolvedValue({ ok: true })
    vi.mocked(detectApps).mockResolvedValue({ apps: [] })
    vi.mocked(appStart).mockResolvedValue({ ok: true })
    vi.mocked(appStatus).mockResolvedValue({ state: 'stopped', running: false, ready: false })
  })

  it('asks once, persists the acknowledgement, and never re-asks after a remount or project switch', async () => {
    const user = userEvent.setup()
    const first = render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)
    await user.click(await first.findByRole('button', { name: /run/i }))
    await waitFor(() => expect(appStart).toHaveBeenCalledTimes(1))
    expect(confirmDialog).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('proxima.ownerpower.ack')).toBe('1')
    first.unmount()

    // Remount on a different project - the single owner already accepted what
    // owner-power execution means; re-asking on every mount was the B6 friction.
    const second = render(<AppRunner token="token" slug="other" onClose={vi.fn()} />)
    await user.click(await second.findByRole('button', { name: /run/i }))
    await waitFor(() => expect(appStart).toHaveBeenCalledTimes(2))
    expect(confirmDialog).toHaveBeenCalledTimes(1)
  })

  it('skips the dialog entirely when the acknowledgement is already persisted', async () => {
    localStorage.setItem('proxima.ownerpower.ack', '1')
    const user = userEvent.setup()
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /run/i }))
    await waitFor(() => expect(appStart).toHaveBeenCalledTimes(1))
    expect(confirmDialog).not.toHaveBeenCalled()
  })

  it('does not persist a declined acknowledgement and asks again on the next Run', async () => {
    const user = userEvent.setup()
    vi.mocked(confirmDialog).mockResolvedValueOnce(false)
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: /run/i }))
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1))
    expect(appStart).not.toHaveBeenCalled()
    expect(localStorage.getItem('proxima.ownerpower.ack')).toBeNull()

    await user.click(screen.getByRole('button', { name: /run/i }))
    await waitFor(() => expect(appStart).toHaveBeenCalledTimes(1))
    expect(confirmDialog).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem('proxima.ownerpower.ack')).toBe('1')
  })
})

describe('AppRunner port pinning', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(getPublicConfig).mockResolvedValue({ apps_domain: null })
    vi.mocked(previewAuth).mockResolvedValue({ ok: true })
    vi.mocked(detectApps).mockResolvedValue({ apps: [] })
    vi.mocked(appStart).mockResolvedValue({ ok: true })
  })

  it('never turns the server-chosen port into a pin', async () => {
    // The status poll reports the port the app actually holds. Writing it into
    // the box would make the next Run demand that exact port, and an ephemeral
    // one is very likely gone by then — the "belongs to another process" trap.
    const user = userEvent.setup()
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ready',
      running: true,
      ready: true,
      requested_port: 41405,
      port: 41405,
      command: 'python3 app.py',
      log: [],
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)
    const box = await screen.findByTitle(/Leave empty to let Proxima pick a free port/i)

    // A healthy run leaves the box on auto and writes nothing to storage, so the
    // next start is free to take a fresh port.
    await waitFor(() => expect(screen.getByRole('button', { name: /Stop/ })).toBeInTheDocument())
    expect(box).toHaveValue(null)
    expect(localStorage.getItem('proxima.appport.v2.demo')).toBeNull()
  })

  it('keeps a port the owner typed', async () => {
    const user = userEvent.setup()
    vi.mocked(appStatus).mockResolvedValue({ state: 'stopped', running: false, ready: false })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)
    const box = await screen.findByTitle(/Leave empty to let Proxima pick a free port/i)
    await user.type(box, '4600')
    await user.click(screen.getByRole('button', { name: /run/i }))
    await waitFor(() => expect(appStart).toHaveBeenCalledWith('token', 'demo', 'npm run dev', 4600, ''))
    expect(localStorage.getItem('proxima.appport.v2.demo')).toBe('4600')
  })

  it.each(['5180', '41405'])('ignores and clears the untrusted legacy pin %s', async (stale) => {
    // The old key held the hardcoded 5180 default, and later whatever port the
    // server auto-assigned (a build echoed it into the box). Neither is a choice
    // the owner made, so both must start on auto — otherwise a preview stays
    // pinned to a port nothing should be holding.
    localStorage.setItem('proxima.appport.demo', stale)
    vi.mocked(appStatus).mockResolvedValue({ state: 'stopped', running: false, ready: false })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)
    const box = await screen.findByTitle(/Leave empty to let Proxima pick a free port/i)
    expect(box).toHaveValue(null)
    expect(localStorage.getItem('proxima.appport.demo')).toBeNull()
  })

  it('still honours a pin the owner set under the current key', async () => {
    localStorage.setItem('proxima.appport.v2.demo', '4600')
    vi.mocked(appStatus).mockResolvedValue({ state: 'stopped', running: false, ready: false })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)
    expect(await screen.findByRole('spinbutton')).toHaveValue(4600)
  })
})

// --- Actionable fail-closed refusals (prune B5, #133) ------------------------
// Governance may refuse; it may never refuse silently. Every refusal state the
// preview panel can show renders the server's next step as its own line, so the
// owner never has to guess what clears it.
describe('AppRunner refusals name the next step', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(getPublicConfig).mockResolvedValue({ apps_domain: null })
    vi.mocked(previewAuth).mockResolvedValue({ ok: true })
    vi.mocked(detectApps).mockResolvedValue({ apps: [] })
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('renders the next step for a port conflict', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'port_conflict',
      running: false,
      ready: false,
      requested_port: 4600,
      conflicting_port: 4600,
      command: 'npm run dev',
      log: [],
      message: 'Port 4600 belongs to another process. Proxima did not open, proxy, or stop it.',
      next_step: 'Stop whatever already holds that port, or press Change port to let Proxima pick a free one.',
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByText(/press Change port to let Proxima pick a free one/i)).toBeInTheDocument()
  })

  it('renders the next step for unverified preview ownership', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ownership_unknown',
      running: true,
      ready: false,
      requested_port: 4600,
      command: 'npm run dev',
      log: [],
      message: 'A server answered on this port and Proxima cannot verify who owns it, so it will not proxy it.',
      next_step: 'Press Stop to release the port, then Run again; if it keeps happening, restart the Proxima server.',
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByText(/Press Stop to release the port/i)).toBeInTheDocument()
  })

  it('renders the next step when the preview output sink refuses', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'stopped',
      running: false,
      ready: false,
      command: 'npm run dev',
      log: [],
      reason: 'output_sink_unavailable',
      message: 'Preview supervisor process identity changed.',
      next_step: 'Press Stop and then Run again; if the refusal repeats, restart the Proxima server.',
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByText(/if the refusal repeats, restart the Proxima server/i)).toBeInTheDocument()
  })
})
