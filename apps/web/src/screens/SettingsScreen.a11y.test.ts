import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import {
  SettingsScreen,
  settingsMenuItemAriaLabel,
  themeSwatchAriaLabel,
} from './SettingsScreen'

vi.mock('./ProjectsScreen', () => ({
  ProjectsScreen: () => 'Migration project detail',
}))

describe('settingsMenuItemAriaLabel', () => {
  it('spaces short label and full hint so names do not smash', () => {
    expect(settingsMenuItemAriaLabel('Account', 'Account, appearance and notifications'))
      .toBe('Account. Account, appearance and notifications')
    expect(settingsMenuItemAriaLabel('Remote', 'Tailscale and Cloudflare setup'))
      .toBe('Remote. Tailscale and Cloudflare setup')
    expect(settingsMenuItemAriaLabel('Agents', 'Runners, goals and prompt modes'))
      .toBe('Agents. Runners, goals and prompt modes')
  })

  it('returns the label alone when hint is empty', () => {
    expect(settingsMenuItemAriaLabel('Diagnostics', '')).toBe('Diagnostics')
    expect(settingsMenuItemAriaLabel('  Media  ', '  ')).toBe('Media')
  })
})

describe('themeSwatchAriaLabel', () => {
  it('marks the selected theme', () => {
    expect(themeSwatchAriaLabel('Sunset', true)).toBe('Sunset, selected')
    expect(themeSwatchAriaLabel('Dark', false)).toBe('Dark')
  })

  it('falls back when label is blank', () => {
    expect(themeSwatchAriaLabel('', true)).toBe('Theme, selected')
  })
})

describe('Settings migration route cleanup', () => {
  it('closes the migration route when another section is selected', async () => {
    const onCloseOpsMigration = vi.fn()
    const user = userEvent.setup()
    render(React.createElement(SettingsScreen, {
      token: 'token',
      user: { id: 1, username: 'owner' },
      profiles: [],
      projects: [],
      activeProject: null,
      opsMigrationSlug: 'legacy-collision',
      onActiveProject: vi.fn(),
      onCloseOpsMigration,
      runners: [],
      features: { masterOrchestrator: false },
      onRefresh: vi.fn().mockResolvedValue(undefined),
      onTokenChange: vi.fn(),
      initialSection: 'projects',
    } as never))

    await user.click(screen.getByRole('button', { name: /Account\./ }))
    expect(onCloseOpsMigration).toHaveBeenCalledOnce()
  })
})
