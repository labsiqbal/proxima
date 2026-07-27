import '@testing-library/jest-dom/vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MasterScreen, resolveMasterProjectSlug } from './MasterScreen'
import { useMasterState } from '../master/MasterStateProvider'
import { getCommandCatalog } from '../api/commands'
import { listArtifacts, listReferenceFiles } from '../api/files'

vi.mock('../master/MasterStateProvider', () => ({ useMasterState: vi.fn() }))
vi.mock('../api/commands', () => ({ getCommandCatalog: vi.fn() }))
vi.mock('../api/files', () => ({
  listReferenceFiles: vi.fn(),
  listArtifacts: vi.fn(),
  uploadFile: vi.fn(),
}))

const actions = {
  setDraft: vi.fn(),
  setSelection: vi.fn(),
  seedDraft: vi.fn(),
  send: vi.fn().mockResolvedValue(undefined),
  setHomeActive: vi.fn(),
  markRead: vi.fn(),
  setSideCollapsed: vi.fn(),
  setScrollState: vi.fn(),
  refresh: vi.fn().mockResolvedValue(undefined),
  updateSettings: vi.fn().mockResolvedValue(undefined),
  clearError: vi.fn(),
}

const state = {
  enabled: true,
  loading: false,
  desk: {
    session: { id: 9, title: 'Master', mode: 'master' },
    master_run: null,
    backing_runner: 'codex',
    jobs: [
      { id: 1, title: 'Queued Task', desk_status: 'queued', status: 'queued', project_name: 'Acme' },
      { id: 2, title: 'Running Task', desk_status: 'running', status: 'running', project_name: 'Acme' },
      { id: 3, title: 'Review Task', desk_status: 'review', status: 'review', project_name: 'Acme' },
      { id: 4, title: 'Completed Task', desk_status: 'done', status: 'done', project_name: 'Acme' },
      { id: 5, title: 'Failed Task', desk_status: 'failed', status: 'failed', project_name: 'Acme' },
    ],
    unattended: false,
    budgets: {
      unattended: false,
      budget_turns: 20,
      budget_wall_seconds: 14400,
      budget_tokens: null,
      tour_core_done: true,
    },
    capacity: { running: 1, max: 3, free: 2, queued: 1 },
    attention: [],
    checkpoints: [],
  },
  messages: [],
  activeRun: null,
  connection: {
    state: 'connected',
    resumeCursor: 12,
    reconnectCount: 0,
    error: '',
  },
  unread: { count: 0 },
  composer: {
    draft: '',
    selection: { start: 0, end: 0 },
    sending: false,
    error: '',
    focusRequest: 0,
  },
  view: {
    homeActive: true,
    sideCollapsed: false,
    scrollTop: 0,
    followTail: true,
    anchorMessageId: null,
  },
  future: {
    focus: { mode: 'fleet', containerId: null, pendingContainerId: null, durable: false },
    target: { mode: 'auto', containerId: null, areaId: null, enabled: false },
    toastQueue: [],
    popup: { open: false, presentation: 'closed', preferredCorner: 'right', enabled: false },
  },
  actions,
}

const runners = [{ id: 'codex', displayName: 'Codex', installed: true, runnable: true }]

describe('resolveMasterProjectSlug', () => {
  it('uses shell Container only for attachments, then an active Task Container', () => {
    expect(resolveMasterProjectSlug({ slug: 'shell' } as never, [{ desk_status: 'running', project_slug: 'task' }])).toBe('shell')
    expect(resolveMasterProjectSlug(null, [{ desk_status: 'queued', project_slug: 'task' }])).toBe('task')
    expect(resolveMasterProjectSlug(null, [{ desk_status: 'done', project_slug: 'old' }])).toBeUndefined()
  })
})

describe('MasterScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useMasterState).mockReturnValue(state as never)
    vi.mocked(getCommandCatalog).mockReturnValue(new Promise(() => {}))
    vi.mocked(listReferenceFiles).mockReturnValue(new Promise(() => {}))
    vi.mocked(listArtifacts).mockReturnValue(new Promise(() => {}))
  })

  it('renders the full Master home, one composer, connection, and every Task state', () => {
    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Master' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Live')
    expect(screen.getAllByRole('textbox', { name: 'Message Master' })).toHaveLength(1)
    expect(screen.getByText('Queued Task')).toBeInTheDocument()
    expect(screen.getByText('Running Task')).toBeInTheDocument()
    expect(screen.getByText('Review Task')).toBeInTheDocument()
    expect(screen.getByText('Completed Task')).toBeInTheDocument()
    expect(screen.getByText('Failed Task')).toBeInTheDocument()
    expect(screen.getByLabelText('Task status summary')).toHaveTextContent('Queued')
    expect(screen.getByLabelText('Master work panel')).toBeInTheDocument()
  })

  it('routes the compact new Task affordance through the provider-owned draft', async () => {
    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'New Task' }))
    expect(actions.seedDraft).toHaveBeenCalledWith('Delegate a new Task: ')
  })

  it('disables the only composer while Master is working', () => {
    vi.mocked(useMasterState).mockReturnValue({
      ...state,
      activeRun: { id: 3, status: 'running' },
      desk: { ...state.desk, master_run: { id: 3, status: 'running' } },
    } as never)
    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    expect(screen.getByRole('textbox', { name: 'Message Master' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Master is working' })).toBeDisabled()
  })

  it('keeps the shared conversation and composer flat while the work panel uses cards', () => {
    const css = readFileSync(resolve(__dirname, '../styles.css'), 'utf8')
    const conversationBlock = css.match(/\.master-conversation\s*\{[^}]+\}/)
    expect(conversationBlock?.[0]).toMatch(/border:\s*0/)
    expect(conversationBlock?.[0]).toMatch(/background:\s*transparent/)
    const sideBlock = css.match(/\.master-side-section\s*\{[^}]+\}/)
    expect(sideBlock?.[0]).toMatch(/border:\s*1px solid/)
    expect(sideBlock?.[0]).toMatch(/border-radius:\s*var\(--radius-lg\)/)
  })

  it('collapses the provider-owned work panel preference', async () => {
    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Hide work panel' }))
    expect(actions.setSideCollapsed).toHaveBeenCalledWith(true)
  })

  it('shows an honest reconnect state without starting a polling fallback', () => {
    vi.mocked(useMasterState).mockReturnValue({
      ...state,
      connection: {
        state: 'retrying',
        resumeCursor: 12,
        reconnectCount: 1,
        error: '',
      },
    } as never)
    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    expect(screen.getByText('Live updates are reconnecting')).toBeInTheDocument()
    expect(screen.getByText(/No polling fallback is running/)).toBeInTheDocument()
  })
})
