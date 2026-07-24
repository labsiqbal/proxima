import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AlphaScreen, resolveAlphaProjectSlug } from './AlphaScreen'
import { getAlphaDesk, sendAlphaMessage } from '../api/alpha'
import { listMessages } from '../api/sessions'
import { getCommandCatalog } from '../api/commands'
import { listArtifacts, listReferenceFiles } from '../api/files'

vi.mock('../api/alpha', () => ({
  getAlphaDesk: vi.fn(), sendAlphaMessage: vi.fn(), saveAlphaSettings: vi.fn(),
  previewCheckpointRestore: vi.fn(), restoreCheckpoint: vi.fn(), setCheckpointPinned: vi.fn(),
}))
vi.mock('../api/sessions', () => ({ listMessages: vi.fn() }))
vi.mock('../api/commands', () => ({ getCommandCatalog: vi.fn() }))
vi.mock('../api/files', () => ({
  listReferenceFiles: vi.fn(),
  listArtifacts: vi.fn(),
  uploadFile: vi.fn(),
}))

const desk = {
  session: { id: 9, title: 'Alpha', mode: 'alpha' },
  alpha_run: null,
  backing_runner: 'pi',
  jobs: [], unattended: false,
  budgets: { unattended: false, budget_turns: 20, budget_wall_seconds: 14400, budget_tokens: null, tour_core_done: true },
  capacity: { running: 0, max: 3, free: 3, queued: 0 },
  attention: [], checkpoints: [],
}
const runners = [{ id: 'pi', displayName: 'Pi', installed: true, runnable: true }]

describe('resolveAlphaProjectSlug', () => {
  it('prefers the shell active project, then an active Alpha job project', () => {
    expect(resolveAlphaProjectSlug({ slug: 'shell' } as never, [{ desk_status: 'running', project_slug: 'job' }])).toBe('shell')
    expect(resolveAlphaProjectSlug(null, [{ desk_status: 'queued', project_slug: 'job' }])).toBe('job')
    expect(resolveAlphaProjectSlug(null, [{ desk_status: 'done', project_slug: 'old' }])).toBeUndefined()
  })
})

describe('AlphaScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(getAlphaDesk).mockResolvedValue(desk as never)
    vi.mocked(listMessages).mockResolvedValue({ messages: [], goal: null })
    vi.mocked(sendAlphaMessage).mockResolvedValue({ run_id: 1, session_id: 9, status: 'queued' })
    vi.mocked(getCommandCatalog).mockResolvedValue({ groups: [] })
    vi.mocked(listReferenceFiles).mockResolvedValue({ files: [{ path: 'docs/brief.md' }], truncated: false })
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [] })
  })

  it('renders the honest empty, capacity, safety, and delegation states', async () => {
    render(<AlphaScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)

    // Header matches Chat/code-header: eyebrow + strong, not a marketing h1.
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Orchestration')).toBeInTheDocument()
    expect(screen.getByText('0 running / 3 free')).toBeInTheDocument()
    expect(screen.getByText('No delegated work')).toBeInTheDocument()
    expect(screen.getByText('Nothing is blocked')).toBeInTheDocument()
    expect(screen.getByText('No checkpoints yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unattended off' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('combobox', { name: 'Backing runner' })).toBeInTheDocument()
    // Chat-like composer stack (not the old alpha-only textarea chrome).
    expect(screen.getByRole('textbox', { name: 'Delegate an outcome' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Attach files' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delegate' })).toBeInTheDocument()
  })

  it('fills an example and guards the async delegation submit through sendAlphaMessage', async () => {
    const user = userEvent.setup()
    render(<AlphaScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Audit this project and delegate independent fixes.' }))
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Delegate an outcome' })).toHaveValue(
        'Audit this project and delegate independent fixes.',
      ),
    )
    await user.click(screen.getByRole('button', { name: 'Delegate' }))

    expect(sendAlphaMessage).toHaveBeenCalledWith('token', 'Audit this project and delegate independent fixes.')
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Delegate an outcome' })).toHaveValue(''))
  })

  it('wires project context into the Chat composer for attach/@ mentions', async () => {
    render(
      <AlphaScreen
        token="token"
        runners={runners as never}
        onOpenJob={vi.fn()}
        activeProject={{ id: 1, name: 'Demo', slug: 'demo', path: '/tmp/demo', visibility: 'private' } as never}
      />,
    )
    await screen.findByText('Alpha')
    await waitFor(() => expect(listReferenceFiles).toHaveBeenCalledWith('token', 'demo'))
    expect(screen.getByRole('button', { name: 'Attach files' })).not.toBeDisabled()
  })

  it('collapses the work panel, persists the preference, and restores it', async () => {
    const user = userEvent.setup()
    render(<AlphaScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    await screen.findByText('No delegated work')

    expect(screen.getByLabelText('Alpha work panel')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Hide work panel' }))
    expect(screen.queryByLabelText('Alpha work panel')).not.toBeInTheDocument()
    expect(localStorage.getItem('proxima.alpha.sideCollapsed')).toBe('1')
    expect(screen.getByRole('button', { name: 'Expand work panel' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Expand work panel' }))
    expect(screen.getByLabelText('Alpha work panel')).toBeInTheDocument()
    expect(localStorage.getItem('proxima.alpha.sideCollapsed')).toBe('0')
  })

  it('disables the Chat composer while Alpha is orchestrating', async () => {
    vi.mocked(getAlphaDesk).mockResolvedValue({
      ...desk,
      alpha_run: { id: 3, status: 'running' },
    } as never)
    render(<AlphaScreen token="token" runners={runners as never} onOpenJob={vi.fn()} />)
    expect(await screen.findByText('Alpha is orchestrating…')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Delegate an outcome' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Alpha is working' })).toBeDisabled()
  })

  it('renders pure tool-result rows as flat timeline text without an outer Proxima bubble', async () => {
    const onOpenJob = vi.fn()
    const toolPayload = JSON.stringify([
      { ok: true, tool: 'capacity', result: {} },
      {
        ok: true, tool: 'list_jobs',
        result: { jobs: [{ id: 7, title: 'Ship notes', status: 'running', engine: 'linear' }] },
      },
      { ok: true, tool: 'dispatch_jobs', result: { jobs: [{ id: 8, title: 'Docs pass', status: 'queued' }] } },
      { ok: false, tool: 'list_projects', error: { message: 'Projects unavailable right now.' } },
    ])
    vi.mocked(listMessages).mockResolvedValue({
      messages: [
        { id: 1, role: 'user', content: 'Audit and delegate.' },
        { id: 2, role: 'system', content: `Alpha tool results:\n\`\`\`json\n${toolPayload}\n\`\`\`` },
        { id: 3, role: 'assistant', content: 'Dispatched the independent work.' },
        { id: 4, role: 'system', content: 'A plain system note stays a bubble.' },
      ],
      goal: null,
    })
    const { container } = render(<AlphaScreen token="token" runners={runners as never} onOpenJob={onOpenJob} />)

    expect(await screen.findByText('Capacity checked')).toBeInTheDocument()
    expect(screen.getByText('Work queue checked')).toBeInTheDocument()
    expect(screen.getByText('Dispatched 1 job')).toBeInTheDocument()
    expect(screen.getByText('Dispatched the independent work.')).toBeInTheDocument()
    expect(screen.getByText('Product action could not run')).toBeInTheDocument()
    expect(screen.getByText('Projects unavailable right now.')).toBeInTheDocument()

    // Flat timeline rows - not nested inside an .alpha-message.system bubble, no card chrome classes required.
    const toolGroup = screen.getByRole('group', { name: 'Alpha tool results' })
    expect(toolGroup).toHaveClass('alpha-tool-results')
    expect(toolGroup.closest('.alpha-message')).toBeNull()
    const toolRows = toolGroup.querySelectorAll(':scope > div')
    expect(toolRows).toHaveLength(4)
    expect(toolRows[0]).toHaveClass('ok')
    expect(toolRows[3]).toHaveClass('failed')
    expect(container.querySelectorAll('.alpha-message.system')).toHaveLength(1)
    expect(screen.getByText('Proxima')).toBeInTheDocument()
    expect(screen.getByText('A plain system note stays a bubble.')).toBeInTheDocument()

    // Job list links still work as plain clickable rows under a dispatch/list tool row.
    await userEvent.setup().click(screen.getByRole('button', { name: /Ship notes/ }))
    expect(onOpenJob).toHaveBeenCalledWith(7, 'linear')
  })
})
