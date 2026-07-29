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
  setFocus: vi.fn().mockResolvedValue(undefined),
  setHistory: vi.fn(),
  setTargetContainer: vi.fn(),
  setTargetArea: vi.fn(),
  loadTargetAreas: vi.fn().mockResolvedValue(undefined),
  openPopup: vi.fn(),
  closePopup: vi.fn(),
  togglePopup: vi.fn(),
  setPopupCorner: vi.fn(),
  dismissToast: vi.fn(),
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
  focus: { mode: 'fleet', containerId: null },
  target: { mode: 'auto', containerId: null, areaId: null },
  popup: { open: false, preferredCorner: 'right' },
  toasts: [],
  fleet: {
    loading: false,
    error: '',
    containers: [],
    areasByContainer: {},
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

  it('does not mount a hidden home composer on another shell surface', () => {
    render(
      <MasterScreen
        token="token"
        runners={runners as never}
        onOpenJob={vi.fn()}
        active={false}
      />,
    )
    expect(screen.queryByRole('textbox', { name: 'Message Master' }))
      .not.toBeInTheDocument()
    expect(actions.setHomeActive).toHaveBeenCalledWith(false)
  })

  it('routes the compact new Task affordance through the provider-owned draft', async () => {
    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'New Task' }))
    expect(actions.seedDraft).toHaveBeenCalledWith('Delegate a new Task: ')
  })

  it('keeps Focus independent from the shell Container and defaults to Master routing', async () => {
    render(
      <MasterScreen
        token="token"
        runners={runners as never}
        activeProject={{ slug: 'shell-container' } as never}
        onOpenJob={vi.fn()}
      />,
    )
    expect(screen.getByRole('combobox', { name: 'Master Focus' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Master message target' }))
      .toHaveValue('')
    expect(screen.getByRole('option', { name: 'Let Master route' }))
      .toBeInTheDocument()
    expect(actions.setFocus).not.toHaveBeenCalled()
  })

  it('only bridges the shell Container into Master after an explicit request', async () => {
    vi.mocked(useMasterState).mockReturnValue({
      ...state,
      fleet: {
        loading: false,
        error: '',
        containers: [{ id: 21, slug: 'shell-container', name: 'Shell', identity_label: 'Shell' }],
        areasByContainer: {},
      },
    } as never)
    render(
      <MasterScreen
        token="token"
        runners={runners as never}
        activeProject={{ slug: 'shell-container' } as never}
        onOpenJob={vi.fn()}
      />,
    )
    expect(actions.setFocus).not.toHaveBeenCalled()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Focus Master here' }))
    expect(actions.setFocus).toHaveBeenCalledWith(21)
    expect(actions.setHistory).toHaveBeenCalledWith({ kind: 'container', containerId: 21 })
  })

  it('shows explicit Container metadata, an advanced Area override, and the Focus warning', async () => {
    vi.mocked(useMasterState).mockReturnValue({
      ...state,
      focus: { mode: 'fleet', containerId: null },
      target: { mode: 'explicit', containerId: 21, areaId: null },
      fleet: {
        loading: false,
        error: '',
        containers: [{
          id: 21,
          slug: 'acme',
          name: 'Acme',
          identity_label: 'Acme',
        }],
        areasByContainer: {
          21: {
            container_id: 21,
            container_slug: 'acme',
            ops_area: { id: 210, kind: 'ops', rel_path: '.proxima/ops' },
            code_areas: [{ id: 211, kind: 'code', rel_path: '.' }],
          },
        },
      },
    } as never)
    const user = userEvent.setup()
    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)

    expect(screen.getByText('Sending will Focus Master on Acme')).toBeInTheDocument()
    await user.click(screen.getByText('Area override (advanced)'))
    expect(screen.getByRole('combobox', { name: 'Target Area override' }))
      .toHaveAccessibleName('Target Area override')
    expect(screen.getByRole('option', { name: 'Master chooses Area' }))
      .toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Operations' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Code: repository root' }))
      .toBeInTheDocument()
  })

  it('keeps deleted Container history selectable by immutable attribution', () => {
    vi.mocked(useMasterState).mockReturnValue({
      ...state,
      messages: [{
        id: 90,
        role: 'assistant',
        content: 'Historical result',
        message_focus: {
          focus_epoch_id: 9,
          focus_container_id: 44,
          subject_container_id: null,
        },
      }],
    } as never)

    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)

    expect(screen.getByRole('option', { name: 'Unavailable Container #44' }))
      .toBeInTheDocument()
  })

  it('labels focused tool-result rows with their shared history attribution', () => {
    vi.mocked(useMasterState).mockReturnValue({
      ...state,
      history: { kind: 'container', containerId: 21 },
      historyMessages: [{
        id: 91,
        role: 'system',
        content: 'Master tool results:\n```json\n[{"ok":true,"tool":"list_tasks","result":{"jobs":[]}}]\n```',
        historyKind: 'focused-segment',
      }],
    } as never)

    render(<MasterScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)

    const results = screen.getByRole('group', { name: 'Master tool results' })
    expect(results.closest('article')).toHaveTextContent('Focused segment')
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
