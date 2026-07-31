import { describe, expect, it } from 'vitest'
import {
  persistWorkProjectPreference,
  readWorkProjectPreference,
  resolveWorkProject,
  workProjectPreferenceKey,
} from './workProjectPreference'

const atlas = {
  slug: 'atlas',
  name: 'Atlas private ops',
  path: '/tmp/atlas',
  owner: 'owner',
  role: 'owner',
  visibility: 'private' as const,
}
const beacon = {
  slug: 'beacon',
  name: 'Beacon release',
  path: '/tmp/beacon',
  owner: 'owner',
  role: 'owner',
  visibility: 'private' as const,
}

describe('Work Project preference', () => {
  it('persists independently per owner', () => {
    persistWorkProjectPreference(7, beacon)
    persistWorkProjectPreference(8, atlas)
    expect(readWorkProjectPreference(7)).toEqual({
      slug: 'beacon',
      name: 'Beacon release',
    })
    expect(readWorkProjectPreference(8)).toEqual({
      slug: 'atlas',
      name: 'Atlas private ops',
    })
    expect(workProjectPreferenceKey(7)).not.toBe(workProjectPreferenceKey(8))
  })

  it('keeps Work usable when browser storage is unavailable', () => {
    expect(() => persistWorkProjectPreference(7, beacon, {
      setItem: () => {
        throw new DOMException('Storage is blocked')
      },
    })).not.toThrow()
  })

  it('restores an existing Project and explicitly reports a removed preference', () => {
    expect(resolveWorkProject([atlas, beacon], {
      slug: 'beacon',
      name: 'Beacon release',
    }, null)).toEqual({
      project: beacon,
      missingPreference: null,
    })
    expect(resolveWorkProject([atlas], {
      slug: 'beacon',
      name: 'Beacon release',
    }, null)).toEqual({
      project: atlas,
      missingPreference: {
        slug: 'beacon',
        name: 'Beacon release',
      },
    })
  })

  it('keeps a newer live Work selection over a stale saved preference', () => {
    expect(resolveWorkProject([atlas, beacon], {
      slug: 'beacon',
      name: 'Beacon release',
    }, atlas)).toEqual({
      project: atlas,
      missingPreference: null,
    })
  })

  it('keeps a permalink-adopted Project while preference still points elsewhere', () => {
    expect(resolveWorkProject([atlas, beacon], {
      slug: 'atlas',
      name: 'Atlas private ops',
    }, beacon)).toEqual({
      project: beacon,
      missingPreference: null,
    })
  })
})
