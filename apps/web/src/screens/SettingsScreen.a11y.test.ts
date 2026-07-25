import { describe, expect, it } from 'vitest'
import { settingsMenuItemAriaLabel, themeSwatchAriaLabel } from './SettingsScreen'

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
