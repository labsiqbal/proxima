import { describe, expect, it } from 'vitest'
import { cronLabelsByWorkflow } from './scheduleBadges'

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
