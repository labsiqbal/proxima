import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getProjectLayout } from '../api/projects'
import { invalidateProjectAreaPaths, useProjectAreaPaths } from './useProjectAreaPaths'

vi.mock('../api/projects', () => ({
  getProjectLayout: vi.fn(),
}))

const layout = (paths: Partial<Record<'wiki' | 'artifacts' | 'scripts' | 'uploads', string>>) => ({
  ops_path: 'ops',
  areas: {
    wiki: { path: paths.wiki ?? 'ops/wiki', source: 'detected' as const, exists: true },
    artifacts: { path: paths.artifacts ?? 'ops/artifacts', source: 'detected' as const, exists: true },
    scripts: { path: paths.scripts ?? 'ops/scripts', source: 'default' as const, exists: false },
    uploads: { path: paths.uploads ?? 'ops/uploads', source: 'default' as const, exists: false },
  },
  memory_writes: { enabled: true },
})

describe('useProjectAreaPaths', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    invalidateProjectAreaPaths()
  })

  it('resolves the mapped container-relative area paths (prune #138)', async () => {
    vi.mocked(getProjectLayout).mockResolvedValue(layout({ wiki: 'wiki' }))
    const { result } = renderHook(() => useProjectAreaPaths('t', 'bip'))
    expect(result.current).toBeNull()
    await waitFor(() => expect(result.current).not.toBeNull())
    expect(result.current).toEqual({
      wiki: 'wiki',
      artifacts: 'ops/artifacts',
      scripts: 'ops/scripts',
      uploads: 'ops/uploads',
    })
  })

  it('caches per slug so repeated mounts do not refetch', async () => {
    vi.mocked(getProjectLayout).mockResolvedValue(layout({}))
    const first = renderHook(() => useProjectAreaPaths('t', 'wingoh'))
    await waitFor(() => expect(first.result.current).not.toBeNull())
    const second = renderHook(() => useProjectAreaPaths('t', 'wingoh'))
    expect(second.result.current).not.toBeNull()
    expect(getProjectLayout).toHaveBeenCalledTimes(1)
  })

  it('falls back to the default names when the layout endpoint fails', async () => {
    vi.mocked(getProjectLayout).mockRejectedValue(new Error('unavailable'))
    const { result } = renderHook(() => useProjectAreaPaths('t', 'broken'))
    await waitFor(() => expect(result.current).not.toBeNull())
    expect(result.current).toEqual({
      wiki: 'wiki',
      artifacts: 'artifacts',
      scripts: 'scripts',
      uploads: 'uploads',
    })
  })
})
