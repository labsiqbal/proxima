import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRunner } from './AppRunner'
import { appExitSummary, appStart, appStatus, appStop, detectApps, getPublicConfig, previewAuth } from '../../api/files'

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
  beforeEach(() => {
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
    vi.mocked(appStart).mockRejectedValue(new Error('Port 5180 is already in use by another process. Choose a different port; Proxima did not stop it.'))
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
      message: 'Proxima cannot verify who owns the listener on this host.',
    })
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByRole('heading', { name: 'Preview ownership could not be verified' })).toBeInTheDocument()
    expect(screen.getByText(/will not proxy this port/i)).toBeInTheDocument()
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
    expect(screen.getByRole('spinbutton')).toHaveValue(5180)
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
