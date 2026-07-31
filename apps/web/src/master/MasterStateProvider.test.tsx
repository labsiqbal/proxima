import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getMasterDesk,
  sendMasterMessage,
  updateMasterFocus,
} from '../api/master'
import { listContainerAreas, listContainers } from '../api/containers'
import { listEvents } from '../api/runs'
import { listMessages } from '../api/sessions'
import {
  MasterStateProvider,
  useMasterState,
} from './MasterStateProvider'

vi.mock('../api/master', () => ({
  getMasterDesk: vi.fn(),
  sendMasterMessage: vi.fn(),
  updateMasterFocus: vi.fn(),
  saveMasterSettings: vi.fn(),
}))
vi.mock('../api/runs', () => ({ listEvents: vi.fn() }))
vi.mock('../api/sessions', () => ({ listMessages: vi.fn() }))
vi.mock('../api/containers', () => ({
  listContainers: vi.fn(),
  listContainerAreas: vi.fn(),
}))

const containers = [{
  id: 21,
  slug: 'acme',
  name: 'Acme',
  identity_label: 'Acme',
  summary: null,
  source_hash: null,
  indexed_at: null,
  last_activity_at: null,
  live: { running_tasks: 0, queued_tasks: 0, open_attention: 0 },
  area_inventory: { total: 2, code: 1, ops: 1 },
  health: {
    registry: 'ready',
    areas: 'ready',
    ops_migration: 'complete',
    graph_freshness: null,
  },
}]

const desk = {
  session: { id: 9, title: 'Master', mode: 'master' },
  master_run: null,
  event_cursor: 12,
  backing_runner: 'codex',
  jobs: [],
  unattended: false,
  budgets: {
    unattended: false,
    budget_turns: 20,
    budget_wall_seconds: 14400,
    budget_tokens: null,
    tour_core_done: true,
  },
  capacity: { running: 0, max: 3, free: 3, queued: 0 },
  attention: [],
  checkpoints: [],
  focus: {
    current_epoch_id: null,
    current_container_id: null,
    pending_container_id: null,
    pending: false,
    version: 0,
  },
}

const fleetProjectionPayload = (messageId: number, taskId: number) => ({
  message_id: messageId,
  task_id: taskId,
  focus_epoch_id: null,
  focus_container_id: null,
  subject_container_id: null,
})

class FakeEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  static instances: FakeEventSource[] = []

  url: string
  withCredentials: boolean
  readyState = FakeEventSource.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close = vi.fn(() => {
    this.readyState = FakeEventSource.CLOSED
  })
  listeners = new Map<string, Set<(event: MessageEvent) => void>>()

  constructor(url: string | URL, init?: EventSourceInit) {
    this.url = String(url)
    this.withCredentials = Boolean(init?.withCredentials)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const handler = listener as (event: MessageEvent) => void
    const listeners = this.listeners.get(type) || new Set()
    listeners.add(handler)
    this.listeners.set(type, listeners)
  }

  open() {
    this.readyState = FakeEventSource.OPEN
    this.onopen?.(new Event('open'))
  }

  fail(closed = false) {
    this.readyState = closed ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING
    this.onerror?.(new Event('error'))
  }

  emit(type: string, payload: unknown) {
    this.emitRaw(type, JSON.stringify(payload))
  }

  emitRaw(type: string, data: string) {
    const event = new MessageEvent(type, { data })
    for (const listener of this.listeners.get(type) || []) listener(event)
  }
}

function Probe() {
  const state = useMasterState()
  return (
    <div>
      <output data-testid="connection">{state.connection.state}</output>
      <output data-testid="conn-error">{state.connection.error}</output>
      <output data-testid="cursor">{state.connection.resumeCursor}</output>
      <output data-testid="messages">{state.messages.map(message => message.content).join('|')}</output>
      <output data-testid="message-ids">{state.messages.map(message => message.id ?? 'pending').join('|')}</output>
      <output data-testid="jobs">{state.desk?.jobs.map(job => `${job.id}:${job.desk_status}:${job.started_at ?? 'null'}:${job.finished_at ?? 'null'}`).join('|')}</output>
      <output data-testid="active-run">{state.activeRun?.status || 'idle'}</output>
      <output data-testid="draft">{state.composer.draft}</output>
      <output data-testid="selection">
        {state.composer.selection.start}:{state.composer.selection.end}
      </output>
      <output data-testid="sending">{String(state.composer.sending)}</output>
      <output data-testid="send-error">{state.composer.error}</output>
      <output data-testid="unread">{state.unread.count}</output>
      <output data-testid="focus">{state.focus.mode}:{state.focus.containerId ?? 'fleet'}</output>
      <output data-testid="target">{state.target.mode}:{state.target.containerId ?? 'auto'}:{state.target.areaId ?? 'any'}</output>
      <output data-testid="popup">{String(state.popup.open)}:{state.popup.preferredCorner}</output>
      <output data-testid="toasts">{state.toasts.map(toast => toast.title).join('|')}</output>
      <output data-testid="fleet-error">{state.fleet.error}</output>
      <output data-testid="fleet-count">{state.fleet.containers.length}</output>
      <output data-testid="scroll">
        {state.view.scrollTop}:{String(state.view.followTail)}:{state.view.anchorMessageId ?? 'none'}
      </output>
      <button type="button" onClick={() => state.actions.setDraft('Keep this draft')}>Draft</button>
      <button
        type="button"
        onClick={() => state.actions.setSelection({ start: 2, end: 7 })}
      >
        Select
      </button>
      <button type="button" onClick={() => void state.actions.send().catch(() => {})}>Send</button>
      <button type="button" onClick={() => void state.actions.refresh()}>Refresh</button>
      <button type="button" onClick={() => state.actions.setTargetContainer(21)}>Target Acme</button>
      <button
        type="button"
        onClick={() => void state.actions.setFocus(21).catch(() => {})}
      >
        Focus Acme
      </button>
      <button type="button" onClick={state.actions.openPopup}>Open popup</button>
      <button type="button" onClick={state.actions.closePopup}>Close popup</button>
      <button
        type="button"
        onClick={() => state.actions.setScrollState({
          scrollTop: 240,
          followTail: false,
          anchorMessageId: 55,
        })}
      >
        Remember scroll
      </button>
    </div>
  )
}

function renderProvider({
  enabled = true,
  token = 'token-a',
  ownerId = 1,
  strict = false,
}: {
  enabled?: boolean
  token?: string
  ownerId?: number
  strict?: boolean
} = {}) {
  const tree = (
    <MasterStateProvider token={token} ownerId={ownerId} enabled={enabled}>
      <Probe />
      <Probe />
    </MasterStateProvider>
  )
  return render(strict ? <React.StrictMode>{tree}</React.StrictMode> : tree)
}

describe('MasterStateProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(getMasterDesk).mockResolvedValue(desk as never)
    vi.mocked(listMessages).mockResolvedValue({ messages: [], goal: null })
    vi.mocked(listContainers).mockResolvedValue({ containers } as never)
    vi.mocked(listContainerAreas).mockResolvedValue({
      container_id: 21,
      container_slug: 'acme',
      ops_area: {
        id: 210,
        kind: 'ops',
        rel_path: '.proxima/ops',
        source: 'auto',
        push_on_merge: false,
        push_remote_url: null,
        remote: null,
      },
      code_areas: [{
        id: 211,
        kind: 'code',
        rel_path: '.',
        source: 'auto',
        push_on_merge: false,
        push_remote_url: null,
        remote: null,
      }],
    } as never)
    vi.mocked(listEvents).mockResolvedValue({
      events: [{
        id: 12,
        seq: 1,
        type: 'run.completed',
        run_id: 2,
        session_id: 9,
        payload: {},
        created_at: '2026-07-27T10:00:00Z',
      }],
    })
    vi.mocked(sendMasterMessage).mockResolvedValue({
      run_id: 40,
      session_id: 9,
      status: 'queued',
      message: {
        id: 41,
        role: 'user',
        content: 'Keep this draft',
        author: 'owner',
        run_id: 40,
        created_at: '2026-07-27T10:02:00Z',
      },
      focus: desk.focus,
    })
    vi.mocked(updateMasterFocus).mockResolvedValue({
      focus: {
        current_epoch_id: 31,
        current_container_id: 21,
        pending_container_id: null,
        pending: false,
        version: 1,
      },
      pending: false,
      changed: true,
    })
  })

  it('is inert while the feature is off', async () => {
    renderProvider({ enabled: false })
    expect(screen.getAllByTestId('connection')[0]).toHaveTextContent('feature-off')
    await new Promise(resolve => window.setTimeout(resolve, 10))
    expect(getMasterDesk).not.toHaveBeenCalled()
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('creates one stream for multiple consumers and React StrictMode', async () => {
    renderProvider({ strict: true })
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(getMasterDesk).toHaveBeenCalledTimes(1)
    expect(listMessages).toHaveBeenCalledTimes(1)
    expect(listEvents).not.toHaveBeenCalled()
    expect(FakeEventSource.instances[0].url).toContain('after_id=12')
    expect(FakeEventSource.instances[0].withCredentials).toBe(true)
  })

  it('bootstraps from a single desk snapshot and streams from its durable cursor', async () => {
    vi.mocked(getMasterDesk).mockResolvedValue({
      ...desk,
      master_run: { id: 22, status: 'running' },
    } as never)
    vi.mocked(listMessages).mockResolvedValue({
      messages: [{
        id: 55,
        role: 'assistant',
        author: 'Master',
        content: 'Arrived during bootstrap',
        run_id: 22,
      }],
      goal: null,
    } as never)

    renderProvider()

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(getMasterDesk).toHaveBeenCalledTimes(1)
    expect(listMessages).toHaveBeenCalledTimes(1)
    expect(screen.getAllByTestId('active-run')[0]).toHaveTextContent('running')
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('Arrived during bootstrap')
    expect(listEvents).not.toHaveBeenCalled()
    expect(FakeEventSource.instances[0].url).toContain('after_id=12')
  })

  it('keeps the durable thread live when the optional Fleet registry is unavailable', async () => {
    localStorage.setItem(
      'proxima.master.focus.1',
      JSON.stringify({ mode: 'container', containerId: 999 }),
    )
    vi.mocked(getMasterDesk).mockResolvedValue({
      ...desk,
      focus: {
        current_epoch_id: 31,
        current_container_id: 21,
        pending_container_id: null,
        pending: false,
        version: 1,
      },
    } as never)
    vi.mocked(listContainers).mockRejectedValue(new Error('fleet unavailable'))

    renderProvider()

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(screen.getAllByTestId('connection')[0]).not.toHaveTextContent('disconnected')
    expect(screen.getAllByTestId('fleet-error')[0]).toHaveTextContent('fleet unavailable')
    expect(screen.getAllByTestId('fleet-count')[0]).toHaveTextContent('0')
    await waitFor(() => {
      expect(screen.getAllByTestId('focus')[0]).toHaveTextContent('container:21')
    })
  })

  it('persists Focus through the versioned API and applies its durable event', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))

    fireEvent.click(screen.getAllByRole('button', { name: 'Focus Acme' })[0])

    await waitFor(() => expect(updateMasterFocus).toHaveBeenCalledWith(
      'token-a',
      21,
      0,
      expect.any(AbortSignal),
    ))
    await waitFor(() => {
      expect(screen.getAllByTestId('focus')[0]).toHaveTextContent('container:21')
    })

    act(() => FakeEventSource.instances[0].emit('master.focus.changed', {
      id: 13,
      seq: 1,
      type: 'master.focus.changed',
      run_id: 0,
      session_id: 9,
      payload: {
        message_id: 61,
        focus_epoch_id: null,
        container_id: null,
        version: 2,
      },
      created_at: '2026-07-27T10:03:00Z',
    }))

    expect(screen.getAllByTestId('focus')[0]).toHaveTextContent('fleet:fleet')
    expect(screen.getAllByTestId('messages')[0])
      .toHaveTextContent('Master Focus changed to Fleet mode.')
  })

  it('retries a failed bootstrap without remounting the authenticated app', async () => {
    vi.mocked(getMasterDesk)
      .mockRejectedValueOnce(new Error('initial desk unavailable'))
      .mockResolvedValue(desk as never)
    renderProvider()
    await waitFor(() => {
      expect(screen.getAllByTestId('connection')[0]).toHaveTextContent('disconnected')
    })

    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(getMasterDesk).toHaveBeenCalledTimes(2)
  })

  it('does not present the latest terminal Master run as active work', async () => {
    vi.mocked(getMasterDesk).mockResolvedValue({
      ...desk,
      master_run: { id: 8, status: 'completed' },
    } as never)
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(screen.getAllByTestId('active-run')[0]).toHaveTextContent('idle')
  })

  it('resumes from the durable cursor and deduplicates replayed projections', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    const event = {
      id: 13,
      seq: 2,
      type: 'master.task.completed',
      run_id: 0,
      session_id: 9,
      payload: fleetProjectionPayload(55, 7),
      created_at: '2026-07-27T10:01:00Z',
    }
    act(() => {
      source.emit('master.task.completed', event)
      source.emit('master.task.completed', event)
    })
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('Completed Task #7.')
    expect(screen.getAllByTestId('messages')[0].textContent?.match(/Completed Task #7\./g)).toHaveLength(1)
    expect(screen.getAllByTestId('jobs')[0]).toHaveTextContent('7:done')
    expect(screen.getAllByTestId('cursor')[0]).toHaveTextContent('13')
  })

  it('projects a production master projection event whose run_id is null', async () => {
    vi.mocked(listEvents).mockResolvedValueOnce({ events: [] })
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => source.emit('master.task.completed', {
      id: 13,
      seq: 2,
      type: 'master.task.completed',
      run_id: null,
      session_id: 9,
      payload: fleetProjectionPayload(55, 7),
      created_at: '2026-07-27T10:01:00Z',
    }))
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('Completed Task #7.')
    expect(screen.getAllByTestId('jobs')[0]).toHaveTextContent('7:done')
    expect(screen.getAllByTestId('cursor')[0]).toHaveTextContent('13')
    expect(listEvents).not.toHaveBeenCalled()
  })

  it('projects checkpoint recovery into Fleet and human-readable history', async () => {
    vi.mocked(getMasterDesk).mockResolvedValue({
      ...desk,
      jobs: [{
        id: 7,
        project_id: 21,
        project_slug: 'acme',
        project_name: 'Acme',
        workflow_id: null,
        session_id: 9,
        origin_master_session_id: 9,
        title: 'Task #7',
        status: 'failed',
        desk_status: 'failed',
        run_status: null,
        engine: 'linear',
        current_step_idx: 0,
        input: {},
        steps_state: [],
        schedule_id: null,
        created_by: null,
        created_at: '2026-07-27T09:00:00Z',
        updated_at: '2026-07-27T09:30:00Z',
        started_at: '2026-07-27T09:05:00Z',
        finished_at: '2026-07-27T09:30:00Z',
        archived_at: null,
        blocked_reason: null,
      }],
    } as never)
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    await waitFor(() => expect(screen.getAllByTestId('jobs')[0]).toHaveTextContent(
      '7:failed:2026-07-27T09:05:00Z:2026-07-27T09:30:00Z',
    ))
    act(() => source.emit('master.task.recovered', {
      id: 13,
      seq: 2,
      type: 'master.task.recovered',
      run_id: null,
      session_id: 9,
      payload: {
        ...fleetProjectionPayload(55, 7),
        task_status: 'queued',
        container_id: 21,
        container_slug: 'acme',
        area_id: 210,
        checkpoint_id: 3,
        actor: { id: 1, username: 'owner' },
        prior_status: 'failed',
        restored_status: 'queued',
        discarded_progress: ['Run #8 (failed) created after the checkpoint'],
        conflicting_progress: [],
      },
      created_at: '2026-07-27T10:01:00Z',
    }))

    expect(screen.getAllByTestId('jobs')[0]).toHaveTextContent('7:queued:null:null')
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent(
      'owner restored Task #7 from checkpoint #3: Failed to Queued.',
    )
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent(
      'Discarded progress: Run #8 (failed) created after the checkpoint.',
    )
  })

  it('retains current Task status when legacy recovery history is corrected', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => {
      source.emit('master.task.completed', {
        id: 13,
        seq: 2,
        type: 'master.task.completed',
        run_id: null,
        session_id: 9,
        payload: fleetProjectionPayload(55, 7),
        created_at: '2026-07-27T10:01:00Z',
      })
      source.emit('master.task.recovery_history_corrected', {
        id: 14,
        seq: 3,
        type: 'master.task.recovery_history_corrected',
        run_id: null,
        session_id: 9,
        payload: {
          ...fleetProjectionPayload(56, 7),
          gap_count: 1,
          first_task_event_id: 30,
          last_task_event_id: 30,
          successor_task_event_id: 31,
          first_successor_task_event_id: 31,
          last_successor_task_event_id: 31,
        },
        created_at: '2026-07-27T10:02:00Z',
      })
    })

    expect(screen.getAllByTestId('jobs')[0]).toHaveTextContent('7:done')
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent(
      'Retained 1 checkpoint recovery audit for Task #7 as a legacy ordering gap across Task events #30-#30 and successor events #31-#31. It was contained without replaying older history after a later publication.',
    )
    expect(screen.getAllByTestId('cursor')[0]).toHaveTextContent('14')
  })

  it('applies a restored rerun after an earlier matching lifecycle', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    const event = (
      id: number,
      type: string,
      messageId: number,
      payload: Record<string, unknown> = {},
    ) => ({
      id,
      seq: id - 11,
      type,
      run_id: null,
      session_id: 9,
      payload: {
        ...fleetProjectionPayload(messageId, 7),
        ...payload,
      },
      created_at: `2026-07-27T10:0${id - 13}:00Z`,
    })

    act(() => {
      source.emit(
        'master.task.completed',
        event(13, 'master.task.completed', 55),
      )
      source.emit('master.task.recovered', event(
        14,
        'master.task.recovered',
        56,
        {
          task_status: 'queued',
          container_id: 21,
          container_slug: 'acme',
          area_id: 210,
          checkpoint_id: 3,
          actor: { id: 1, username: 'owner' },
          prior_status: 'done',
          restored_status: 'queued',
          discarded_progress: [],
          conflicting_progress: [],
        },
      ))
      source.emit(
        'master.task.started',
        event(15, 'master.task.started', 57),
      )
      source.emit(
        'master.task.review_ready',
        event(16, 'master.task.review_ready', 58),
      )
      source.emit(
        'master.task.completed',
        event(17, 'master.task.completed', 59),
      )
    })

    expect(screen.getAllByTestId('jobs')[0]).toHaveTextContent('7:done')
    expect(
      screen.getAllByTestId('messages')[0].textContent
        ?.match(/Completed Task #7\./g),
    ).toHaveLength(2)
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent(
      'owner restored Task #7 from checkpoint #3: Done to Queued.',
    )
    expect(screen.getAllByTestId('cursor')[0]).toHaveTextContent('17')
  })

  it('projects a production master projection event whose run_id is absent', async () => {
    vi.mocked(listEvents).mockResolvedValueOnce({ events: [] })
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => source.emitRaw(
      'master.task.review_ready',
      JSON.stringify({
        id: 14,
        seq: 3,
        type: 'master.task.review_ready',
        session_id: 9,
        payload: fleetProjectionPayload(56, 8),
        created_at: '2026-07-27T10:02:00Z',
      }),
    ))
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('Task #8 is ready for review.')
    expect(screen.getAllByTestId('cursor')[0]).toHaveTextContent('14')
    expect(listEvents).not.toHaveBeenCalled()
  })

  it('advances the cursor without surfacing raw delta or tool payload material', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.emit('message.delta', {
      id: 13,
      seq: 2,
      type: 'message.delta',
      run_id: 2,
      session_id: 9,
      payload: { text: 'secret token and /private/path' },
      created_at: '2026-07-27T10:01:00Z',
    }))
    act(() => source.emit('tool.complete', {
      id: 14,
      seq: 3,
      type: 'tool.complete',
      run_id: 2,
      session_id: 9,
      payload: { output: 'raw tool output' },
      created_at: '2026-07-27T10:01:01Z',
    }))

    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('')
    expect(screen.getAllByTestId('cursor')[0]).toHaveTextContent('14')
  })

  it('reconciles once after disconnect and reconnect without opening another stream', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => source.fail())
    expect(screen.getAllByTestId('connection')[0]).toHaveTextContent('retrying')
    act(() => source.open())
    await waitFor(() => expect(getMasterDesk).toHaveBeenCalledTimes(2))
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('does not let a stale reconciliation snapshot clear a newer live run', async () => {
    let resolveDelta!: (value: { events: never[] }) => void
    const liveEvent = {
      id: 13,
      seq: 1,
      type: 'run.started',
      run_id: 40,
      session_id: 9,
      payload: {},
      created_at: '2026-07-27T10:01:00Z',
    }
    vi.mocked(listEvents).mockReturnValueOnce(new Promise(resolve => {
      resolveDelta = resolve as typeof resolveDelta
    }))
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => source.fail())
    act(() => source.open())
    await waitFor(() => expect(getMasterDesk).toHaveBeenCalledTimes(2))

    act(() => source.emit('run.started', liveEvent))
    expect(screen.getAllByTestId('active-run')[0]).toHaveTextContent('running')
    act(() => resolveDelta({ events: [liveEvent] as never[] }))

    await waitFor(() => {
      expect(screen.getAllByTestId('active-run')[0]).toHaveTextContent('running')
    })
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('ignores a late terminal event for an older run', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.emit('run.started', {
      id: 13,
      seq: 1,
      type: 'run.started',
      run_id: 40,
      session_id: 9,
      payload: {},
      created_at: '2026-07-27T10:01:00Z',
    }))
    act(() => source.emit('run.completed', {
      id: 14,
      seq: 1,
      type: 'run.completed',
      run_id: 39,
      session_id: 9,
      payload: {},
      created_at: '2026-07-27T10:01:01Z',
    }))

    expect(screen.getAllByTestId('active-run')[0]).toHaveTextContent('running')
  })

  it('does not reconcile an intact stream merely because a run terminated', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.emit('run.started', {
      id: 13,
      seq: 1,
      type: 'run.started',
      run_id: 40,
      session_id: 9,
      payload: {},
      created_at: '2026-07-27T10:01:00Z',
    }))
    act(() => source.emit('run.completed', {
      id: 14,
      seq: 2,
      type: 'run.completed',
      run_id: 40,
      session_id: 9,
      payload: {},
      created_at: '2026-07-27T10:01:01Z',
    }))

    expect(screen.getAllByTestId('active-run')[0]).toHaveTextContent('idle')
    await new Promise(resolve => window.setTimeout(resolve, 10))
    expect(listEvents).not.toHaveBeenCalled()
  })

  it('keeps durable messages ordered when a replay fills a snapshot gap', async () => {
    vi.mocked(listMessages).mockResolvedValue({
      messages: [
        { id: 3, role: 'assistant', content: 'three' },
        { id: 1, role: 'user', content: 'one' },
      ],
      goal: null,
    } as never)
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    act(() => FakeEventSource.instances[0].emit('message.complete', {
      id: 13,
      seq: 1,
      type: 'message.complete',
      run_id: 3,
      session_id: 9,
      payload: { message_id: 2, text: 'two' },
      created_at: '2026-07-27T10:01:00Z',
    }))

    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('one|two|three')
  })

  it('keeps a connected label when reconciliation fails but the live stream stays open', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    vi.mocked(getMasterDesk).mockRejectedValueOnce(new Error('server offline'))
    act(() => source.fail())
    act(() => source.open())

    await waitFor(() => {
      expect(screen.getAllByTestId('conn-error')[0])
        .toHaveTextContent('could not be reconciled')
    })
    expect(screen.getAllByTestId('connection')[0]).toHaveTextContent('connected')
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('reports disconnected when reconnect reconciliation fails after the stream closes', async () => {
    let rejectDesk!: (reason: Error) => void
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => source.fail())
    vi.mocked(getMasterDesk).mockReturnValueOnce(
      new Promise((_, reject) => { rejectDesk = reject }) as never,
    )
    act(() => source.open())
    act(() => { source.readyState = FakeEventSource.CLOSED })
    act(() => rejectDesk(new Error('server offline')))

    await waitFor(() => {
      expect(screen.getAllByTestId('connection')[0]).toHaveTextContent('disconnected')
    })
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('restarts a fatally closed stream when the owner retries', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => source.fail(true))
    expect(screen.getAllByTestId('connection')[0]).toHaveTextContent('disconnected')

    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2))
    expect(source.close).toHaveBeenCalled()
    expect(FakeEventSource.instances.filter(instance => !instance.close.mock.calls.length))
      .toHaveLength(1)
  })

  it('coalesces a reconnect storm into bounded reconciliation on one stream', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    act(() => {
      for (let attempt = 0; attempt < 8; attempt += 1) {
        source.fail()
        source.open()
      }
    })

    await waitFor(() => expect(listEvents.mock.calls.length).toBeGreaterThanOrEqual(2))
    await new Promise(resolve => window.setTimeout(resolve, 10))
    expect(listEvents.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(listEvents.mock.calls.length).toBeLessThanOrEqual(3)
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('ignores malformed stream data and reconciles from the durable cursor', async () => {
    vi.mocked(listEvents).mockResolvedValueOnce({ events: [] })
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))

    act(() => FakeEventSource.instances[0].emitRaw('warning', '{"id":13'))

    await waitFor(() => {
      expect(listEvents).toHaveBeenLastCalledWith(
        'token-a',
        9,
        12,
        expect.any(AbortSignal),
      )
    })
    expect(screen.getAllByTestId('cursor')[0]).toHaveTextContent('12')
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('detects a per-run sequence gap and reconciles from the prior cursor', async () => {
    vi.mocked(listEvents).mockResolvedValueOnce({ events: [] })
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    act(() => FakeEventSource.instances[0].emit('run.queued', {
      id: 13,
      seq: 1,
      type: 'run.queued',
      run_id: 22,
      session_id: 9,
      payload: {},
      created_at: '2026-07-27T10:00:00Z',
    }))
    act(() => FakeEventSource.instances[0].emit('run.started', {
      id: 14,
      seq: 3,
      type: 'run.started',
      run_id: 22,
      session_id: 9,
      payload: {},
      created_at: '2026-07-27T10:01:00Z',
    }))
    await waitFor(() => expect(listEvents).toHaveBeenLastCalledWith('token-a', 9, 13, expect.any(AbortSignal)))
  })

  it('aborts and ignores an initial response after owner and token transition', async () => {
    let resolveFirst!: (value: typeof desk) => void
    vi.mocked(getMasterDesk).mockImplementation((token) => {
      if (token === 'token-a') {
        return new Promise(resolve => { resolveFirst = resolve }) as never
      }
      return Promise.resolve({
        ...desk,
        session: { ...desk.session, id: 10 },
      }) as never
    })
    const view = renderProvider()
    await waitFor(() => expect(getMasterDesk).toHaveBeenCalledWith('token-a', expect.any(AbortSignal)))
    view.rerender(
      <MasterStateProvider token="token-b" ownerId={2} enabled>
        <Probe />
      </MasterStateProvider>,
    )
    act(() => resolveFirst(desk))
    await waitFor(() => expect(getMasterDesk).toHaveBeenCalledWith('token-b', expect.any(AbortSignal)))
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(FakeEventSource.instances[0].url).toContain('/sessions/10/')
  })

  it('closes the old stream while preserving refresh state on same-owner token rotation', async () => {
    const view = renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    act(() => source.emit('message.complete', {
      id: 13,
      seq: 1,
      type: 'message.complete',
      run_id: 3,
      session_id: 9,
      payload: { message_id: 2, text: 'old owner state' },
      created_at: '2026-07-27T10:01:00Z',
    }))

    view.rerender(
      <MasterStateProvider token="token-b" ownerId={1} enabled>
        <Probe />
      </MasterStateProvider>,
    )

    expect(source.close).toHaveBeenCalled()
    expect(screen.getByTestId('draft')).toHaveTextContent('Keep this draft')
    expect(screen.getByTestId('messages')).toHaveTextContent('')
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2))
    expect(FakeEventSource.instances.filter(instance => !instance.close.mock.calls.length))
      .toHaveLength(1)
  })

  it('closes the old stream and clears state when disabled', async () => {
    const view = renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    view.rerender(
      <MasterStateProvider token="token-a" ownerId={1} enabled={false}>
        <Probe />
      </MasterStateProvider>,
    )
    expect(source.close).toHaveBeenCalled()
    expect(screen.getByTestId('messages')).toHaveTextContent('')
    expect(screen.getByTestId('connection')).toHaveTextContent('feature-off')
  })

  it('preserves provider-owned draft and selection while authenticated routes change', async () => {
    const view = renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[0])

    view.rerender(
      <MasterStateProvider token="token-a" ownerId={1} enabled>
        <Probe />
      </MasterStateProvider>,
    )

    expect(screen.getByTestId('draft')).toHaveTextContent('Keep this draft')
    expect(screen.getByTestId('selection')).toHaveTextContent('2:7')
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('restores owner-scoped draft, selection, target, and scroll after refresh', async () => {
    const first = renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Target Acme' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Remember scroll' })[0])
    first.unmount()

    renderProvider()

    expect(screen.getAllByTestId('draft')[0]).toHaveTextContent('Keep this draft')
    expect(screen.getAllByTestId('selection')[0]).toHaveTextContent('2:7')
    expect(screen.getAllByTestId('target')[0]).toHaveTextContent('explicit:21:any')
    expect(screen.getAllByTestId('scroll')[0]).toHaveTextContent('240:false:55')
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2))
    expect(FakeEventSource.instances.filter(instance => !instance.close.mock.calls.length))
      .toHaveLength(1)
  })

  it('focuses the explicit target before one enqueue and preserves it across route consumers', async () => {
    vi.mocked(sendMasterMessage).mockResolvedValueOnce({
      run_id: 40,
      session_id: 9,
      status: 'queued',
      message: {
        id: 41,
        role: 'user',
        content: 'Keep this draft',
        author: 'owner',
        run_id: 40,
        created_at: '2026-07-27T10:02:00Z',
      },
      focus: {
        current_epoch_id: 31,
        current_container_id: 21,
        pending_container_id: null,
        pending: false,
        version: 1,
      },
    })
    const view = renderProvider({ strict: true })
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    fireEvent.click(screen.getAllByRole('button', { name: 'Target Acme' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Send' })[0])

    await waitFor(() => {
      expect(screen.getAllByTestId('focus')[0]).toHaveTextContent('container:21')
    })
    expect(sendMasterMessage).toHaveBeenCalledTimes(1)
    expect(sendMasterMessage).toHaveBeenCalledWith(
      'token-a',
      'Keep this draft',
      {
        focus: { mode: 'container', container_id: 21 },
        target: { mode: 'explicit', container_id: 21, area_id: undefined },
      },
      expect.any(AbortSignal),
    )

    view.rerender(
      <React.StrictMode>
        <MasterStateProvider token="token-a" ownerId={1} enabled>
          <Probe />
        </MasterStateProvider>
      </React.StrictMode>,
    )
    expect(screen.getByTestId('focus')).toHaveTextContent('container:21')
    expect(screen.getByTestId('target')).toHaveTextContent('explicit:21:any')
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('keeps popup visibility provider-owned without opening another stream', async () => {
    renderProvider({ strict: true })
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Remember scroll' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Open popup' })[0])
    expect(screen.getAllByTestId('popup')[0]).toHaveTextContent('true:right')
    expect(screen.getAllByTestId('draft')[0]).toHaveTextContent('Keep this draft')
    expect(screen.getAllByTestId('scroll')[0]).toHaveTextContent('240:false:55')
    fireEvent.click(screen.getAllByRole('button', { name: 'Close popup' })[0])
    expect(screen.getAllByTestId('popup')[0]).toHaveTextContent('false:right')
    expect(screen.getAllByTestId('scroll')[0]).toHaveTextContent('240:false:55')
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('coalesces Task progress and emits one toast per durable completion transition', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    const base = {
      seq: 1,
      run_id: 0,
      session_id: 9,
      created_at: '2026-07-27T10:04:00Z',
    }
    act(() => {
      source.emit('master.task.started', {
        ...base,
        id: 13,
        type: 'master.task.started',
        payload: fleetProjectionPayload(70, 8),
      })
      source.emit('master.task.started', {
        ...base,
        id: 14,
        seq: 2,
        type: 'master.task.started',
        payload: fleetProjectionPayload(71, 8),
      })
      source.emit('master.task.completed', {
        ...base,
        id: 15,
        seq: 3,
        type: 'master.task.completed',
        payload: fleetProjectionPayload(72, 8),
      })
      source.emit('master.task.completed', {
        ...base,
        id: 16,
        seq: 4,
        type: 'master.task.completed',
        payload: fleetProjectionPayload(72, 8),
      })
      source.emit('message.delta', {
        ...base,
        id: 17,
        seq: 5,
        type: 'message.delta',
        payload: { message_id: 72, text: 'raw token' },
      })
    })

    expect(screen.getAllByTestId('toasts')[0].textContent)
      .toBe('Task #8 started|Task #8 completed')
  })

  it('submits once, shows pending state, and clears the draft only after acceptance', async () => {
    let resolveSend!: (value: Awaited<ReturnType<typeof sendMasterMessage>>) => void
    vi.mocked(sendMasterMessage).mockReturnValue(
      new Promise(resolve => { resolveSend = resolve }),
    )
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Send' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Send' })[1])
    expect(sendMasterMessage).toHaveBeenCalledTimes(1)
    expect(screen.getAllByTestId('draft')[0]).toHaveTextContent('Keep this draft')
    expect(screen.getAllByTestId('sending')[0]).toHaveTextContent('true')
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('Keep this draft')
    act(() => resolveSend({
      run_id: 40,
      session_id: 9,
      status: 'queued',
      message: {
        id: 41,
        role: 'user',
        content: 'Keep this draft',
        author: 'owner',
        run_id: 40,
        created_at: '2026-07-27T10:02:00Z',
      },
      focus: desk.focus,
    }))
    await waitFor(() => expect(screen.getAllByTestId('draft')[0]).toHaveTextContent(''))
    expect(screen.getAllByTestId('sending')[0]).toHaveTextContent('false')
    act(() => FakeEventSource.instances[0].emit('message.complete', {
      id: 13,
      seq: 2,
      type: 'message.complete',
      run_id: 40,
      session_id: 9,
      payload: { message_id: 42, text: 'Accepted reply' },
      created_at: '2026-07-27T10:02:01Z',
    }))
    expect(screen.getAllByTestId('messages')[0])
      .toHaveTextContent('Keep this draft|Accepted reply')
  })

  it('collapses the optimistic send into the persisted row when reconciliation lands mid-send', async () => {
    let resolveSend!: (value: Awaited<ReturnType<typeof sendMasterMessage>>) => void
    vi.mocked(sendMasterMessage).mockReturnValue(
      new Promise(resolve => { resolveSend = resolve }),
    )
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())

    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Send' })[0])
    expect(sendMasterMessage).toHaveBeenCalledTimes(1)
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('Keep this draft')
    expect(screen.getAllByTestId('message-ids')[0]).toHaveTextContent('pending')

    vi.mocked(listMessages).mockResolvedValueOnce({
      messages: [{
        id: 41,
        role: 'user',
        content: 'Keep this draft',
        author: 'owner',
        run_id: 40,
        created_at: '2026-07-27T10:02:00Z',
      }],
      goal: null,
    } as never)
    vi.mocked(listEvents).mockResolvedValueOnce({ events: [] })
    act(() => source.fail())
    act(() => source.open())
    await waitFor(() => expect(getMasterDesk).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getAllByTestId('message-ids')[0].textContent).toBe('41'))
    expect(screen.getAllByTestId('messages')[0].textContent).toBe('Keep this draft')

    act(() => resolveSend({
      run_id: 40,
      session_id: 9,
      status: 'queued',
      message: {
        id: 41,
        role: 'user',
        content: 'Keep this draft',
        author: 'owner',
        run_id: 40,
        created_at: '2026-07-27T10:02:00Z',
      },
      focus: desk.focus,
    }))
    await waitFor(() => expect(screen.getAllByTestId('draft')[0]).toHaveTextContent(''))
    expect(screen.getAllByTestId('message-ids')[0].textContent).toBe('41')
    expect(screen.getAllByTestId('sending')[0]).toHaveTextContent('false')

    act(() => source.emit('message.complete', {
      id: 44,
      seq: 2,
      type: 'message.complete',
      run_id: 40,
      session_id: 9,
      payload: { message_id: 42, text: 'Accepted reply' },
      created_at: '2026-07-27T10:02:01Z',
    }))
    expect(screen.getAllByTestId('messages')[0].textContent)
      .toBe('Keep this draft|Accepted reply')
    expect(screen.getAllByTestId('message-ids')[0].textContent).toBe('41|42')
  })

  it('keeps the draft and removes the pending row after a send error', async () => {
    vi.mocked(sendMasterMessage).mockRejectedValue(new Error('server unavailable'))
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    fireEvent.click(screen.getAllByRole('button', { name: 'Draft' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'Send' })[0])
    await waitFor(() => expect(screen.getAllByTestId('send-error')[0]).toHaveTextContent('server unavailable'))
    expect(screen.getAllByTestId('draft')[0]).toHaveTextContent('Keep this draft')
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('')
  })
})
