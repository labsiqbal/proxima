import { describe, expect, it } from 'vitest'
import { cronLabelsByWorkflow, howItRunsBadges } from './scheduleBadges'

describe('howItRunsBadges', () => {
  it('shows Manual only when not scheduled', () => {
    expect(howItRunsBadges({ scheduled: false })).toEqual([
      { kind: 'manual', label: 'Manual' },
    ])
  })

  it('shows Manual + Scheduled when schedules exist', () => {
    expect(howItRunsBadges({ scheduled: true })).toEqual([
      { kind: 'manual', label: 'Manual' },
      { kind: 'scheduled', label: 'Scheduled' },
    ])
  })

  it('appends short cron text when a single cadence is known', () => {
    expect(howItRunsBadges({ scheduled: true, cronLabels: ['every hour'] })).toEqual([
      { kind: 'manual', label: 'Manual' },
      { kind: 'scheduled', label: 'Scheduled · every hour' },
    ])
  })

  it('omits cron suffix when multiple distinct cadences exist', () => {
    expect(howItRunsBadges({ scheduled: true, cronLabels: ['every hour', 'daily'] })).toEqual([
      { kind: 'manual', label: 'Manual' },
      { kind: 'scheduled', label: 'Scheduled' },
    ])
  })
})

describe('cronLabelsByWorkflow', () => {
  it('groups cron hints per workflow id', () => {
    const map = cronLabelsByWorkflow(
      [
        { workflow_id: 1, cron: '0 * * * *' },
        { workflow_id: 1, cron: '0 9 * * *' },
        { workflow_id: 2, cron: '0 * * * *' },
      ],
      cron => (cron === '0 * * * *' ? 'every hour' : cron),
    )
    expect(map.get(1)).toEqual(['every hour', '0 9 * * *'])
    expect(map.get(2)).toEqual(['every hour'])
  })
})
