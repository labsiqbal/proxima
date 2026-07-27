import { describe, expect, it } from 'vitest'
import { SETTINGS_GROUPS, settingsSectionOrder } from './SettingsScreen'

describe('Settings IA groups (UI Flow M7)', () => {
  it('orders Work setup · Integrations · System · Help', () => {
    expect(SETTINGS_GROUPS.map(g => g.label)).toEqual([
      'Work setup',
      'Integrations',
      'System',
      'Help',
    ])
  })

  it('places Projects, Agents, Master under Work setup', () => {
    const work = SETTINGS_GROUPS.find(g => g.id === 'work')
    expect(work?.keys).toEqual(expect.arrayContaining(['projects', 'agents', 'master']))
  })

  it('places Media and Remote under Integrations', () => {
    expect(SETTINGS_GROUPS.find(g => g.id === 'integrations')?.keys).toEqual(['media', 'remote'])
  })

  it('places Account and Diagnostics under System', () => {
    expect(SETTINGS_GROUPS.find(g => g.id === 'system')?.keys).toEqual(['account', 'diagnostics'])
  })

  it('ends with Help as its own group', () => {
    const order = settingsSectionOrder()
    expect(order[order.length - 1]).toBe('help')
  })
})
