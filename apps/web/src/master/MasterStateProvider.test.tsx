import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getMasterDesk, sendMasterMessage } from '../api/master'
import { listEvents } from '../api/runs'
import { listMessages } from '../api/sessions'
import {
  MasterStateProvider,
  useMasterState,
} from './MasterStateProvider'

vi.mock('../api/master', () => ({
  getMasterDesk: vi.fn(),
  sendMasterMessage: vi.fn(),
  saveMasterSettings: vi.fn(),
}))
vi.mock('../api/runs', () => ({ listEvents: vi.fn() }))
vi.mock('../api/sessions', () => ({ listMessages: vi.fn() }))

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
}

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
      <output data-testid="cursor">{state.connection.resumeCursor}</output>
      <output data-testid="messages">{state.messages.map(message => message.content).join('|')}</output>
      <output data-testid="jobs">{state.desk?.jobs.map(job => `${job.id}:${job.desk_status}`).join('|')}</output>
      <output data-testid="active-run">{state.activeRun?.status || 'idle'}</output>
      <output data-testid="draft">{state.composer.draft}</output>
      <output data-testid="selection">
        {state.composer.selection.start}:{state.composer.selection.end}
      </output>
      <output data-testid="sending">{String(state.composer.sending)}</output>
      <output data-testid="send-error">{state.composer.error}</output>
      <output data-testid="unread">{state.unread.count}</output>
      <button type="button" onClick={() => state.actions.setDraft('Keep this draft')}>Draft</button>
      <button
        type="button"
        onClick={() => state.actions.setSelection({ start: 2, end: 7 })}
      >
        Select
      </button>
      <button type="button" onClick={() => void state.actions.send().catch(() => {})}>Send</button>
      <button type="button" onClick={() => void state.actions.refresh()}>Refresh</button>
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
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(getMasterDesk).mockResolvedValue(desk as never)
    vi.mocked(listMessages).mockResolvedValue({ messages: [], goal: null })
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
    expect(getMasterDesk).toHaveBeenCalledTimes(2)
    expect(listMessages).toHaveBeenCalledTimes(1)
    expect(listEvents).not.toHaveBeenCalled()
    expect(FakeEventSource.instances[0].url).toContain('after_id=12')
    expect(FakeEventSource.instances[0].withCredentials).toBe(true)
  })

  it('takes the durable desk cursor before the authoritative bootstrap snapshots', async () => {
    vi.mocked(getMasterDesk)
      .mockResolvedValueOnce(desk as never)
      .mockResolvedValueOnce({
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
    expect(screen.getAllByTestId('active-run')[0]).toHaveTextContent('running')
    expect(screen.getAllByTestId('messages')[0]).toHaveTextContent('Arrived during bootstrap')
    expect(getMasterDesk.mock.invocationCallOrder[0])
      .toBeLessThan(getMasterDesk.mock.invocationCallOrder[1])
    expect(listEvents).not.toHaveBeenCalled()
    expect(FakeEventSource.instances[0].url).toContain('after_id=12')
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
    expect(getMasterDesk).toHaveBeenCalledTimes(3)
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
      payload: { message_id: 55, task_id: 7 },
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
    await waitFor(() => expect(getMasterDesk).toHaveBeenCalledTimes(3))
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
    await waitFor(() => expect(getMasterDesk).toHaveBeenCalledTimes(3))

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

  it('reports a disconnected state when reconnect reconciliation cannot recover', async () => {
    renderProvider()
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const source = FakeEventSource.instances[0]
    act(() => source.open())
    vi.mocked(getMasterDesk).mockRejectedValueOnce(new Error('server offline'))
    act(() => source.fail())
    act(() => source.open())

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

    await waitFor(() => expect(listEvents).toHaveBeenCalledTimes(2))
    await new Promise(resolve => window.setTimeout(resolve, 10))
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

  it('closes the old stream and clears owned state on same-owner token rotation', async () => {
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
    expect(screen.getByTestId('draft')).toHaveTextContent('')
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
