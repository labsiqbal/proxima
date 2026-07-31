import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getOpsMigration, retryOpsMigration, validateOpsMigration } from '../../api/projects'
import type { OpsMigrationDetail as Detail, Project } from '../../types'
import { confirmDialog } from '../ui/Dialog'
import { OpsMigrationDetail } from './OpsMigrationDetail'

vi.mock('../../api/projects', () => ({
  getOpsMigration: vi.fn(),
  validateOpsMigration: vi.fn(),
  retryOpsMigration: vi.fn(),
}))
vi.mock('../ui/Dialog', () => ({ confirmDialog: vi.fn() }))

const project: Project = {
  slug: 'legacy-collision',
  name: 'Legacy collision',
  path: '/owner/legacy-collision',
  owner: 'owner',
  role: 'owner',
  visibility: 'private',
}

const collision: Detail = {
  project: { id: 9, slug: project.slug, name: project.name },
  phase: 'attention',
  migration_version: 1,
  stored_reason: 'physical Ops root is not empty',
  active_ops_path: '.',
  legacy_owned_paths: [{
    path: 'wiki',
    destination: 'ops/wiki',
    expected_kind: 'directory',
    legacy_state: 'directory',
    physical_state: 'directory',
    layout: 'both',
    usable_from_active_ops: true,
  }],
  physical_ops: {
    path: 'ops',
    state: 'populated',
    entries: [{ path: 'ops/wiki', kind: 'directory' }],
  },
  inspection: {
    legacy_root: { inspectable: true, reason: null },
    physical_root: { inspectable: true, reason: null },
  },
  conflicts: [{ path: 'wiki', reason: 'Both wiki and ops/wiki exist.' }],
  retry_safe: false,
  validation_reason: 'physical Ops root is not empty',
  what_remains_usable: {
    legacy_ops_active: true,
    physical_ops_active: false,
    available_paths: ['wiki'],
    unavailable_paths: [],
    summary: 'The legacy Ops layout remains active.',
  },
  attention: {
    status: 'open',
    created_at: '2026-07-31T00:00:00Z',
    resolved_at: null,
  },
  timestamps: {
    started_at: '2026-07-31T00:00:00Z',
    completed_at: null,
    updated_at: '2026-07-31T00:00:00Z',
  },
}

const safe = {
  ...collision,
  retry_safe: true,
  validation_reason: null,
  conflicts: [],
  legacy_owned_paths: [{
    ...collision.legacy_owned_paths[0],
    physical_state: 'missing',
    layout: 'legacy' as const,
  }],
  physical_ops: { path: 'ops', state: 'missing', entries: [] },
}

const complete: Detail = {
  ...safe,
  phase: 'complete',
  active_ops_path: 'ops',
  retry_safe: false,
  validation_reason: 'Migration is already complete; no retry is needed.',
  legacy_owned_paths: [{
    ...safe.legacy_owned_paths[0],
    legacy_state: 'missing',
    physical_state: 'directory',
    layout: 'physical',
    usable_from_active_ops: true,
  }],
  attention: { ...safe.attention, status: 'resolved', resolved_at: '2026-07-31T00:05:00Z' },
  what_remains_usable: {
    legacy_ops_active: false,
    physical_ops_active: true,
    available_paths: ['wiki'],
    unavailable_paths: [],
    summary: 'Physical ops/ is active and Ops features remain usable.',
  },
}

describe('OpsMigrationDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getOpsMigration).mockResolvedValue(collision)
    vi.mocked(validateOpsMigration).mockResolvedValue(collision)
    vi.mocked(retryOpsMigration).mockResolvedValue(complete)
    vi.mocked(confirmDialog).mockResolvedValue(true)
  })

  it('shows exact durable state and keeps retry unavailable for a collision', async () => {
    render(<OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={vi.fn()} />)

    expect(await screen.findAllByText('physical Ops root is not empty')).toHaveLength(2)
    expect(screen.getByRole('heading', { name: 'Ops migration' })).toHaveFocus()
    expect(screen.getByText('Both wiki and ops/wiki exist.')).toBeInTheDocument()
    const physicalEntries = screen.getByRole('list', { name: 'Physical ops entries' })
    expect(within(physicalEntries).getByText('ops/wiki')).toBeInTheDocument()
    expect(within(physicalEntries).getByText('directory')).toBeInTheDocument()
    expect(screen.getByText('The legacy Ops layout remains active.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry migration' })).toBeDisabled()
    expect(screen.getByText(/never merges, overwrites, deletes, follows symlinks/i)).toBeInTheDocument()
  })

  it('reveals each side without changing either layout', async () => {
    const events: unknown[] = []
    const listener = (event: Event) => events.push((event as CustomEvent).detail)
    window.addEventListener('proxima:reveal-file', listener)
    const user = userEvent.setup()
    render(<OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Reveal legacy side' }))
    await user.click(screen.getByRole('button', { name: 'Reveal physical ops/' }))
    await user.click(screen.getByRole('button', { name: 'Reveal legacy wiki' }))
    await user.click(screen.getByRole('button', { name: 'Reveal physical ops/wiki' }))
    expect(events).toEqual([
      { path: '', pathKind: 'root', projectSlug: project.slug, rootSide: 'container' },
      { path: 'ops', pathKind: 'directory', projectSlug: project.slug, rootSide: 'container' },
      { path: 'wiki', pathKind: 'directory', projectSlug: project.slug, rootSide: 'container' },
      { path: 'ops/wiki', pathKind: 'directory', projectSlug: project.slug, rootSide: 'container' },
    ])
    window.removeEventListener('proxima:reveal-file', listener)
  })

  it('uses each side actual kind and disables unsafe collision targets', async () => {
    vi.mocked(getOpsMigration).mockResolvedValue({
      ...collision,
      legacy_owned_paths: [
        {
          ...collision.legacy_owned_paths[0],
          legacy_state: 'file',
          physical_state: 'directory',
        },
        {
          path: 'artifacts',
          destination: 'ops/artifacts',
          expected_kind: 'directory',
          legacy_state: 'symlink',
          physical_state: 'unavailable',
          layout: 'both',
          usable_from_active_ops: false,
        },
      ],
    })
    const events: unknown[] = []
    const listener = (event: Event) => events.push((event as CustomEvent).detail)
    window.addEventListener('proxima:reveal-file', listener)
    const user = userEvent.setup()
    render(<OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Reveal legacy wiki' }))
    await user.click(screen.getByRole('button', { name: 'Reveal physical ops/wiki' }))
    expect(events).toEqual([
      { path: 'wiki', pathKind: 'file', projectSlug: project.slug, rootSide: 'container' },
      { path: 'ops/wiki', pathKind: 'directory', projectSlug: project.slug, rootSide: 'container' },
    ])
    expect(screen.getByRole('button', { name: 'Reveal legacy artifacts' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reveal physical ops/artifacts' })).toBeDisabled()
    window.removeEventListener('proxima:reveal-file', listener)
  })

  it('disables unavailable legacy-root inspection with an accessible reason', async () => {
    vi.mocked(getOpsMigration).mockResolvedValue({
      ...collision,
      inspection: {
        legacy_root: {
          inspectable: false,
          reason: 'Container root is missing',
        },
        physical_root: {
          inspectable: false,
          reason: 'Physical ops/ is unavailable',
        },
      },
    })
    render(<OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={vi.fn()} />)

    const reveal = await screen.findByRole('button', { name: 'Reveal legacy side' })
    expect(reveal).toBeDisabled()
    expect(reveal).toHaveAccessibleDescription('Container root is missing')
  })

  it('clears the previous project payload while the next request fails', async () => {
    const nextProject = { ...project, slug: 'next-project', name: 'Next project' }
    vi.mocked(getOpsMigration).mockImplementation((_token, slug) => {
      if (slug === project.slug) return Promise.resolve(collision)
      return Promise.reject(new Error('next project unavailable'))
    })
    const view = render(
      <OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={vi.fn()} />,
    )
    expect(await screen.findAllByText('physical Ops root is not empty')).toHaveLength(2)

    view.rerender(
      <OpsMigrationDetail token="token" project={nextProject} onBack={vi.fn()} onChanged={vi.fn()} />,
    )
    expect(await screen.findByText('next project unavailable')).toBeInTheDocument()
    expect(screen.queryByText('physical Ops root is not empty')).not.toBeInTheDocument()
    expect(screen.getByText('Next project')).toBeInTheDocument()
  })

  it('enables an explicit retry only after refreshed validation is safe', async () => {
    vi.mocked(validateOpsMigration).mockResolvedValue(safe)
    const changed = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={changed} />)

    await user.click(await screen.findByRole('button', { name: 'Refresh validation' }))
    expect(await screen.findByText('Validation is safe. Retry is now available.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reveal physical ops/' })).toBeDisabled()
    const retry = screen.getByRole('button', { name: 'Retry migration' })
    expect(retry).toBeEnabled()
    await user.click(retry)
    await waitFor(() => expect(retryOpsMigration).toHaveBeenCalledWith('token', project.slug))
    expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringContaining('move only the planned Ops-owned paths'),
    }))
    expect(await screen.findByText('Ops migration completed. The Attention item is resolved.')).toBeInTheDocument()
    expect(changed).toHaveBeenCalled()
  })

  it('uses revalidate-only confirmation copy for repaired physical layouts', async () => {
    const repaired: Detail = {
      ...complete,
      retry_safe: true,
      attention: { ...complete.attention, status: 'open', resolved_at: null },
      validation_reason: null,
    }
    vi.mocked(getOpsMigration).mockResolvedValue(repaired)
    const user = userEvent.setup()
    render(<OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Retry migration' }))
    expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringContaining('revalidate ownership and layout and resolve Attention without moving content'),
    }))
    const message = vi.mocked(confirmDialog).mock.calls[0]?.[0]?.message || ''
    expect(message).not.toContain('move only the planned Ops-owned paths')
  })

  it('ignores stale reload responses after a newer request starts', async () => {
    let resolveStale: ((value: Detail) => void) | undefined
    const stale = new Promise<Detail>((resolve) => { resolveStale = resolve })
    const nextProject = { ...project, slug: 'repaired-layout', name: 'Repaired layout' }
    const repaired: Detail = {
      ...complete,
      project: { id: 12, slug: nextProject.slug, name: nextProject.name },
      retry_safe: true,
      attention: { ...complete.attention, status: 'open', resolved_at: null },
      validation_reason: null,
    }
    vi.mocked(getOpsMigration).mockImplementation((_token, slug) => {
      if (slug === project.slug) return stale
      return Promise.resolve(repaired)
    })
    const view = render(
      <OpsMigrationDetail token="token" project={project} onBack={vi.fn()} onChanged={vi.fn()} />,
    )
    expect(await screen.findByRole('heading', { name: 'Ops migration' })).toBeInTheDocument()

    view.rerender(
      <OpsMigrationDetail token="token" project={nextProject} onBack={vi.fn()} onChanged={vi.fn()} />,
    )
    expect(await screen.findByText('Repaired layout')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry migration' })).toBeEnabled()

    resolveStale?.(collision)
    await waitFor(() => {
      expect(screen.queryByText('Both wiki and ops/wiki exist.')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Repaired layout')).toBeInTheDocument()
    expect(screen.getByText('Physical ops/ is active and Ops features remain usable.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry migration' })).toBeEnabled()
  })
})
