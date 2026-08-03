import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppViewport } from './AppViewport'
import { appStatus, getPublicConfig, previewAuth } from '../../api/files'
import { OPEN_RUN_PREVIEW_EVENT } from '../../lib/runPreview'

vi.mock('../../api/files', () => ({
  appStatus: vi.fn(),
  appViewUrl: (slug: string) => `/api/appview/${slug}/`,
  getPublicConfig: vi.fn(),
  previewAuth: vi.fn(),
}))

// The app preview renders in the Artifacts main window (#147, ADR-0043
// decision 4). The security model is #140/ADR-0042's, unchanged: the frame
// carries the same sandbox it carried in the dock, and only an origin that is
// not Proxima's ever gets `allow-same-origin`.
describe('AppViewport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPublicConfig).mockResolvedValue({ apps_domain: null })
    vi.mocked(previewAuth).mockResolvedValue({ ok: true })
    vi.mocked(appStatus).mockResolvedValue({ state: 'stopped', running: false, ready: false })
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('frames the running app with the isolated-origin sandbox, byte for byte', async () => {
    vi.stubGlobal('location', { hostname: '100.101.102.103', protocol: 'http:' })
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ready', running: true, ready: true, port: 5180, preview_port: 43123, command: 'npm run dev', log: [],
    })
    render(<AppViewport token="t" slug="demo" onClose={vi.fn()} />)

    const frame = await screen.findByTitle('App preview')
    expect(frame).toHaveAttribute('src', expect.stringContaining('http://100.101.102.103:43123/'))
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts allow-same-origin allow-forms allow-popups allow-modals')
  })

  it('keeps the same-origin proxy frame opaque - no allow-same-origin', async () => {
    vi.stubGlobal('location', { hostname: 'linc.example.ts.net', protocol: 'https:' })
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ready', running: true, ready: true, port: 5180, preview_port: 43123, command: 'npm run dev', log: [],
    })
    render(<AppViewport token="t" slug="demo" onClose={vi.fn()} />)

    const frame = await screen.findByTitle('App preview')
    expect(frame).toHaveAttribute('src', expect.stringContaining('/api/appview/demo/'))
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts allow-forms allow-popups allow-modals')
  })

  it('mints the preview cookie so the frame loads without a fresh Access login', async () => {
    render(<AppViewport token="t" slug="demo" onClose={vi.fn()} />)
    await waitFor(() => expect(previewAuth).toHaveBeenCalledWith('t'))
  })

  it('shows the app coming up instead of an empty frame', async () => {
    vi.mocked(appStatus).mockResolvedValue({
      state: 'starting', running: true, ready: false, prolonged_start: false, command: 'npm run dev', log: [],
    })
    render(<AppViewport token="t" slug="demo" onClose={vi.fn()} />)

    expect(await screen.findByText(/Starting your app/i)).toBeInTheDocument()
    expect(screen.queryByTitle('App preview')).not.toBeInTheDocument()
  })

  it('reflects a stopped app and keeps the way back to the gallery', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    vi.mocked(appStatus).mockResolvedValue({
      state: 'stopped', running: false, ready: false, command: 'npm run dev', log: [],
    })
    render(<AppViewport token="t" slug="demo" backLabel="Gallery" onClose={onClose} />)

    expect(await screen.findByRole('heading', { name: 'This app is not running' })).toBeInTheDocument()
    expect(screen.queryByTitle('App preview')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back to gallery' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('drops the frame the moment the app stops, without leaving a stale page up', async () => {
    vi.mocked(appStatus)
      .mockResolvedValueOnce({ state: 'ready', running: true, ready: true, port: 5180, preview_port: 43123, command: 'npm run dev', log: [] })
      .mockResolvedValue({ state: 'stopped', running: false, ready: false, command: 'npm run dev', log: [] })
    vi.stubGlobal('location', { hostname: 'localhost', protocol: 'http:' })
    render(<AppViewport token="t" slug="demo" onClose={vi.fn()} />)

    await screen.findByTitle('App preview')
    await waitFor(() => expect(screen.queryByTitle('App preview')).not.toBeInTheDocument(), { timeout: 4000 })
    expect(screen.getByRole('heading', { name: 'This app is not running' })).toBeInTheDocument()
  })

  it('points at the Run controls rather than dead-ending, in every state', async () => {
    const user = userEvent.setup()
    const heard = vi.fn()
    window.addEventListener(OPEN_RUN_PREVIEW_EVENT, heard)
    vi.mocked(appStatus).mockResolvedValue({
      state: 'stopped', running: false, ready: false, command: 'npm run dev', log: [],
    })
    render(<AppViewport token="t" slug="demo" onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Run controls' }))
    expect(heard).toHaveBeenCalled()
    window.removeEventListener(OPEN_RUN_PREVIEW_EVENT, heard)
  })

  it('carries the device presets and reload the dock used to hold', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('location', { hostname: 'localhost', protocol: 'http:' })
    vi.mocked(appStatus).mockResolvedValue({
      state: 'ready', running: true, ready: true, port: 5180, preview_port: 43123, command: 'npm run dev', log: [],
    })
    render(<AppViewport token="t" slug="demo" onClose={vi.fn()} />)

    const frame = await screen.findByTitle('App preview')
    const firstSrc = frame.getAttribute('src')
    expect(screen.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute('href', 'http://localhost:43123/')
    await user.click(screen.getByRole('button', { name: 'Mobile' }))
    expect(screen.getByTestId('app-viewport-stage')).toHaveStyle({ width: '390px' })
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    // A reload is a new frame with a fresh cache-buster, never a silent no-op.
    await waitFor(() => expect(screen.getByTitle('App preview').getAttribute('src')).not.toBe(firstSrc))
  })
})
