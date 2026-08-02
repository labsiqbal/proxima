import { describe, expect, it } from 'vitest'
import {
  resolveDesignStudioProject,
  taskLinkedDesignProjectSlug,
} from './designStudioProject'

const atlas = {
  slug: 'atlas',
  name: 'Atlas private ops',
  path: '/tmp/atlas',
  owner: 'owner',
  visibility: 'private' as const,
}
const beacon = {
  slug: 'beacon',
  name: 'Beacon release',
  path: '/tmp/beacon',
  owner: 'owner',
  visibility: 'private' as const,
}

describe('Design Studio Project scope', () => {
  it('opens Task-linked designs on the Task owner without adopting Work', () => {
    expect(resolveDesignStudioProject([atlas, beacon], 'beacon', atlas)).toEqual(beacon)
    expect(resolveDesignStudioProject([atlas, beacon], null, atlas)).toEqual(atlas)
    expect(resolveDesignStudioProject([atlas, beacon], 'missing', atlas)).toEqual(atlas)
  })

  it('prefers the Task deliverable owning slug over stale Task context', () => {
    expect(taskLinkedDesignProjectSlug('beacon', 'atlas')).toBe('beacon')
    expect(taskLinkedDesignProjectSlug(null, 'beacon')).toBe('beacon')
    expect(taskLinkedDesignProjectSlug(undefined, null)).toBe(null)
  })
})
