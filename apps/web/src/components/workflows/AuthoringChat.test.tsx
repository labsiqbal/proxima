import '@testing-library/jest-dom/vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import React from 'react'
import { appliedRunKey, AuthoringChat, type WorkflowChatHandle } from './AuthoringChat'
import { listMessages } from '../../api/sessions'
import { createRun } from '../../api/runs'

const restoreMock = vi.fn()
const setBusyRunMock = vi.fn()
const setEventsMock = vi.fn()

vi.mock('../../api/sessions', () => ({
  listMessages: vi.fn(),
}))
vi.mock('../../api/runs', () => ({
  createRun: vi.fn(),
}))
vi.mock('../../hooks/useRunStream', () => ({
  useRunStream: () => ({
    events: [],
    setEvents: setEventsMock,
    busyRun: null,
    setBusyRun: setBusyRunMock,
    restore: restoreMock,
  }),
}))
vi.mock('../chat/ChatThread', () => ({
  ChatThread: () => <div data-testid="chat-thread" />,
}))
vi.mock('../chat/Composer', () => ({
  Composer: () => <div data-testid="composer" />,
}))

const features = {
  design_studio: false,
  image_gen: false,
  multi_agent: false,
  wiki: false,
} as never

describe('AuthoringChat', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    vi.mocked(listMessages).mockResolvedValue({ messages: [], goal: null })
    vi.mocked(createRun).mockResolvedValue({ run_id: 9, session_id: 5, status: 'queued' } as never)
    restoreMock.mockResolvedValue({ events: [], lastRun: null, running: false, completed: false })
  })

  it('Start chat opens the thread and leaves Opening…', async () => {
    const user = userEvent.setup()
    render(
      <AuthoringChat
        token="t"
        features={features}
        profiles={[]}
        activeProfile={null}
        projectSlug="demo"
        ensureSession={async () => 5}
        buildPrompt={text => text}
        applyReply={() => false}
        stripBlock={raw => raw}
        idleHint="Hint"
        placeholder="Type…"
      />,
    )
    expect(screen.getByRole('button', { name: 'Start chat' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Start chat' }))
    await waitFor(() => expect(screen.getByTestId('chat-thread')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Opening…' })).not.toBeInTheDocument()
    expect(listMessages).toHaveBeenCalledWith('t', 5)
    expect(restoreMock).toHaveBeenCalledWith(5)
  })

  it('concurrent open callers share one session load instead of racing Opening…', async () => {
    let release!: (id: number) => void
    const gate = new Promise<number>(resolve => { release = resolve })
    const ensure = vi.fn(() => gate)
    const ref = React.createRef<WorkflowChatHandle>()

    render(
      <AuthoringChat
        ref={ref}
        token="t"
        features={features}
        profiles={[]}
        activeProfile={null}
        projectSlug="demo"
        ensureSession={ensure}
        buildPrompt={text => text}
        applyReply={() => false}
        stripBlock={raw => raw}
        buildTestPrompt={index => `test ${index}`}
        idleHint="Hint"
        placeholder="Type…"
      />,
    )

    const user = userEvent.setup()
    // Kick Start chat and Test-in-chat (via imperative handle) while ensure is still pending.
    await user.click(screen.getByRole('button', { name: /Start chat|Opening/ }))
    expect(screen.getByRole('button', { name: 'Opening…' })).toBeDisabled()
    await act(async () => {
      ref.current?.runThrough(0, 'Step A')
    })
    expect(ensure).toHaveBeenCalledTimes(1)

    await act(async () => { release(5) })
    await waitFor(() => expect(screen.getByTestId('chat-thread')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Opening…' })).not.toBeInTheDocument()
    await waitFor(() => expect(createRun).toHaveBeenCalled())
    expect(createRun.mock.calls[0][1]).toBe(5)
  })

  it('surfaces an error when the plan has no chat session instead of a silent idle card', async () => {
    const user = userEvent.setup()
    render(
      <AuthoringChat
        token="t"
        features={features}
        profiles={[]}
        activeProfile={null}
        projectSlug="demo"
        ensureSession={async () => null}
        buildPrompt={text => text}
        applyReply={() => false}
        stripBlock={raw => raw}
        idleHint="Hint"
        placeholder="Type…"
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Start chat' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not open the plan chat/)
    expect(screen.getByRole('button', { name: 'Start chat' })).toBeEnabled()
  })

  it('autoOpen hydrates the session on mount without a Start chat click', async () => {
    render(
      <AuthoringChat
        token="t"
        features={features}
        profiles={[]}
        activeProfile={null}
        projectSlug="demo"
        ensureSession={async () => 5}
        buildPrompt={text => text}
        applyReply={() => false}
        stripBlock={raw => raw}
        idleHint="Hint"
        placeholder="Type…"
        autoOpen
      />,
    )
    await waitFor(() => expect(screen.getByTestId('chat-thread')).toBeInTheDocument())
    expect(restoreMock).toHaveBeenCalledWith(5)
  })

  it('re-attaches busyRun when restore reports a live authoring run', async () => {
    restoreMock.mockResolvedValue({
      events: [{ id: 1, type: 'run.queued', run_id: 42, session_id: 5, payload: {}, created_at: '' }],
      lastRun: 42,
      running: true,
      completed: false,
    })
    vi.mocked(listMessages).mockResolvedValue({
      messages: [{ id: 1, role: 'user', content: 'draw it', run_id: 42 }],
      goal: null,
    })

    render(
      <AuthoringChat
        token="t"
        features={features}
        profiles={[]}
        activeProfile={null}
        projectSlug="demo"
        ensureSession={async () => 5}
        buildPrompt={text => text}
        applyReply={() => false}
        stripBlock={raw => raw}
        idleHint="Hint"
        placeholder="Type…"
        autoOpen
      />,
    )

    await waitFor(() => expect(setBusyRunMock).toHaveBeenCalledWith(42))
    expect(setEventsMock).toHaveBeenCalled()
  })

  it('applyReply recovers a graph that finished while the panel was closed', async () => {
    const applyReply = vi.fn(() => true)
    restoreMock.mockResolvedValue({
      events: [{ id: 2, type: 'run.completed', run_id: 7, session_id: 5, payload: {}, created_at: '' }],
      lastRun: 7,
      running: false,
      completed: true,
    })
    vi.mocked(listMessages).mockResolvedValue({
      messages: [
        { id: 1, role: 'user', content: 'add parallel posts', run_id: 7 },
        {
          id: 2,
          role: 'assistant',
          run_id: 7,
          content: 'Done.\n<workflow-graph>{"graph":{"nodes":[{"id":"a","name":"A","instruction":"do"}],"edges":[]}}</workflow-graph>',
        },
      ],
      goal: null,
    })

    render(
      <AuthoringChat
        token="t"
        features={features}
        profiles={[]}
        activeProfile={null}
        projectSlug="demo"
        ensureSession={async () => 5}
        buildPrompt={text => text}
        applyReply={applyReply}
        stripBlock={raw => raw.replace(/<workflow-graph[\s\S]*?<\/workflow-graph>/gi, '').trim()}
        idleHint="Hint"
        placeholder="Type…"
        autoOpen
      />,
    )

    await waitFor(() => expect(applyReply).toHaveBeenCalled())
    expect(sessionStorage.getItem(appliedRunKey(5))).toBe('7')
  })

  it('does not re-apply a completed run already recorded as applied', async () => {
    sessionStorage.setItem(appliedRunKey(5), '7')
    const applyReply = vi.fn(() => true)
    restoreMock.mockResolvedValue({
      events: [],
      lastRun: 7,
      running: false,
      completed: true,
    })
    vi.mocked(listMessages).mockResolvedValue({
      messages: [
        { id: 1, role: 'user', content: 'draw', run_id: 7 },
        { id: 2, role: 'assistant', run_id: 7, content: '<workflow-graph>{"nodes":[{"id":"a","instruction":"x"}]}</workflow-graph>' },
      ],
      goal: null,
    })

    render(
      <AuthoringChat
        token="t"
        features={features}
        profiles={[]}
        activeProfile={null}
        projectSlug="demo"
        ensureSession={async () => 5}
        buildPrompt={text => text}
        applyReply={applyReply}
        stripBlock={raw => raw}
        idleHint="Hint"
        placeholder="Type…"
        autoOpen
      />,
    )

    await waitFor(() => expect(screen.getByTestId('chat-thread')).toBeInTheDocument())
    expect(applyReply).not.toHaveBeenCalled()
  })
})
