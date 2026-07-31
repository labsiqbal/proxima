import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PlatformSupportPanel } from './SettingsScreen'

const getPlatformSupport = vi.fn()

vi.mock('../api/platformSupport', () => ({
  getPlatformSupport: () => getPlatformSupport(),
}))

describe('PlatformSupportPanel', () => {
  beforeEach(() => {
    getPlatformSupport.mockResolvedValue({
      claim: 'linux-first-daily-driver',
      server: {
        key: 'linux',
        label: 'Linux',
        tier: 'supported',
        summary: 'Daily-driver server support.',
      },
      platforms: [
        { key: 'linux', label: 'Linux', tier: 'supported', summary: 'Daily-driver server support.' },
        { key: 'macos', label: 'macOS', tier: 'experimental', summary: 'LaunchAgent packaging.' },
        { key: 'windows', label: 'Windows', tier: 'experimental', summary: 'Scheduled Task packaging.' },
      ],
      reference: 'docs/linux-daily-driver-acceptance.md',
    })
  })

  it('shows the end-user support tiers and detected server', async () => {
    render(<PlatformSupportPanel />)

    expect(await screen.findByText('Linux server')).toBeVisible()
    const rows = screen.getByLabelText('Platform support levels')
    expect(rows).toHaveTextContent('LinuxSupported')
    expect(rows).toHaveTextContent('macOSExperimental')
    expect(rows).toHaveTextContent('WindowsExperimental')
    expect(screen.getByText(/Qualified daily-driver target/)).toHaveTextContent('CachyOS')
    expect(screen.getByText(/This server:/)).toHaveTextContent('Linux supported')
  })
})
