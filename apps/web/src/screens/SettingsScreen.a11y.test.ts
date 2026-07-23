import { describe, expect, it } from 'vitest'
import { settingsMenuItemAriaLabel, themeSwatchAriaLabel } from './SettingsScreen'

describe('settingsMenuItemAriaLabel', () => {
  it('spaces label and hint so names do not smash', () => {
    expect(settingsMenuItemAriaLabel('Account & Preferences', 'Account, appearance and notifications'))
      .toBe('Account & Preferences. Account, appearance and notifications')
    expect(settingsMenuItemAriaLabel('Remote Access', 'Tailscale and Cloudflare setup'))
      .toBe('Remote Access. Tailscale and Cloudflare setup')
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
