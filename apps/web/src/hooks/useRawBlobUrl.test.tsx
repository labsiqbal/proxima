import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchRawBlob: vi.fn(),
  revoke: vi.fn(),
}))

vi.mock('../api/files', () => ({
  fetchRawBlob: (...args: unknown[]) => mocks.fetchRawBlob(...args),
}))

import { useRawBlobUrl } from './useRawBlobUrl'

describe('useRawBlobUrl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchRawBlob.mockResolvedValue('blob:ready')
    vi.stubGlobal('URL', {
      ...URL,
      revokeObjectURL: mocks.revoke,
    })
  })

  it('models loading, ready, failed, retry, and cleanup', async () => {
    const target = {
      project: 'identity',
      area: { kind: 'ops' as const, id: 7 },
      path: 'brand/mark.svg',
    }
    const { result, unmount, rerender } = renderHook(
      ({ path }: { path: string }) => useRawBlobUrl('token', 'identity', path, target),
      { initialProps: { path: 'brand/mark.svg' } },
    )

    expect(result.current.status).toBe('loading')
    expect(result.current.url).toBeNull()

    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.url).toBe('blob:ready')
    expect(mocks.fetchRawBlob).toHaveBeenCalledWith(
      'token',
      'identity',
      'brand/mark.svg',
      target,
    )

    mocks.fetchRawBlob.mockRejectedValueOnce(new Error('nope'))
    act(() => {
      result.current.retry()
    })
    expect(result.current.status).toBe('loading')
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.url).toBeNull()

    mocks.fetchRawBlob.mockResolvedValueOnce('blob:retry')
    act(() => {
      result.current.retry()
    })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.url).toBe('blob:retry')

    rerender({ path: 'brand/other.svg' })
    await waitFor(() => expect(mocks.revoke).toHaveBeenCalledWith('blob:retry'))
    unmount()
  })

  it('stays idle without auth context', () => {
    const { result } = renderHook(() => useRawBlobUrl(undefined, undefined, 'x.svg'))
    expect(result.current).toMatchObject({ url: null, status: 'idle' })
    expect(mocks.fetchRawBlob).not.toHaveBeenCalled()
  })
})
