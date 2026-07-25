import { describe, expect, it } from 'vitest'
import { resolveOwnedProjectSlug } from './GraphScreen'

describe('resolveOwnedProjectSlug', () => {
  it('prefers the owned workflow/job project over the shell active project', () => {
    expect(resolveOwnedProjectSlug({ project_slug: 'owned' }, 'shell')).toBe('owned')
  })

  it('falls back to the shell project when ownership is missing', () => {
    expect(resolveOwnedProjectSlug({ project_slug: null }, 'shell')).toBe('shell')
    expect(resolveOwnedProjectSlug(undefined, 'shell')).toBe('shell')
  })

  it('returns null when neither source has a project', () => {
    expect(resolveOwnedProjectSlug(null, null)).toBe(null)
    expect(resolveOwnedProjectSlug({ project_slug: '  ' }, '  ')).toBe(null)
  })
})
