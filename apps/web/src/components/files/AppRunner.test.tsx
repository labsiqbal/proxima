import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRunner } from './AppRunner'
import { appStart, appStatus, detectApps, getPublicConfig, previewAuth } from '../../api/files'

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
    vi.mocked(appStatus).mockResolvedValue({ running: false })
  })

  it('shows a port collision instead of presenting a foreign preview', async () => {
    const user = userEvent.setup()
    vi.mocked(appStart).mockRejectedValue(new Error('Port 5180 is already in use by another process. Choose a different port; Proxima did not stop it.'))
    render(<AppRunner token="token" slug="demo" onClose={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /run/i }))

    await waitFor(() => expect(screen.getByText(/Port 5180 is already in use/)).toBeInTheDocument())
    expect(appStart).toHaveBeenCalledWith('token', 'demo', 'npm run dev', 5180, '')
  })
})
