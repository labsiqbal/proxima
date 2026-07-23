import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  SearchModal,
  compactSearchLabel,
  isDesignSearchHit,
  searchChatAriaLabel,
  searchMessageAriaLabel,
  searchProjectAriaLabel,
  sessionFromSearchHit,
} from './SearchModal'
import type { ChatSession } from '../../types'

const searchMock = vi.fn()

vi.mock('../../api/search', () => ({
  search: (...args: unknown[]) => searchMock(...args),
}))

const baseSession = (over: Partial<ChatSession> = {}): ChatSession => ({
  id: 1,
  title: 'Ordinary chat',
  runner_id: 'hermes',
  visibility: 'private',
  mode: 'chat',
  project_slug: 'demo-scratch',
  project_name: 'Demo Scratch',
  ...over,
})

describe('search accessible labels', () => {
  it('joins parts with middle dots and truncates long labels', () => {
    expect(compactSearchLabel(['Hello', 'world'])).toBe('Hello · world')
    expect(compactSearchLabel(['a', '', null, 'b'])).toBe('a · b')
    const long = 'x'.repeat(200)
    expect(compactSearchLabel([long], 40).endsWith('…')).toBe(true)
    expect(compactSearchLabel([long], 40).length).toBeLessThanOrEqual(40)
  })

  it('builds project / chat / message labels without dumping full bodies', () => {
    expect(searchProjectAriaLabel('Demo Scratch', 'demo-scratch')).toBe('Demo Scratch · demo-scratch')
    expect(searchProjectAriaLabel('Same', 'Same')).toBe('Same')
    expect(searchChatAriaLabel('Ice cream', 'Demo', true)).toBe('Ice cream · Demo · Design')
    const label = searchMessageAriaLabel(
      'Ice cream flavors',
      'assistant',
      '# Brainstorm result\n\n**Prompt:** long markdown body that should not flood the accessible name forever',
    )
    expect(label.startsWith('Ice cream flavors · assistant:')).toBe(true)
    expect(label.length).toBeLessThanOrEqual(160)
  })
})

describe('sessionFromSearchHit / isDesignSearchHit', () => {
  it('prefers the live sessions list entry', () => {
    const live = baseSession({ id: 12, title: 'Live title', mode: 'chat' })
    const hit = sessionFromSearchHit({ id: 12, title: 'Search title', mode: 'design' }, [live])
    expect(hit).toBe(live)
  })

  it('builds a design session from search fields when missing from the list', () => {
    const hit = sessionFromSearchHit({
      id: 12,
      title: 'Design: farewell card',
      mode: 'design',
      project_slug: 'demo-scratch',
      project_name: 'Demo Scratch',
    }, [])
    expect(hit.mode).toBe('design')
    expect(hit.project_slug).toBe('demo-scratch')
    expect(isDesignSearchHit(hit)).toBe(true)
  })

  it('infers design from the Design: title prefix when mode is absent', () => {
    expect(isDesignSearchHit({ title: 'Design: launch' })).toBe(true)
    expect(sessionFromSearchHit({ id: 3, title: 'Design: launch' }, []).mode).toBe('design')
  })
})

describe('SearchModal open routing', () => {
  beforeEach(() => {
    searchMock.mockReset()
  })

  it('opens design hits via onOpenDesign even when not in the sessions list', async () => {
    const user = userEvent.setup()
    searchMock.mockResolvedValue({
      projects: [],
      chats: [{
        id: 12,
        title: 'Design: A farewell announcement card',
        mode: 'design',
        project_slug: 'demo-scratch',
        project_name: 'Demo Scratch',
      }],
      messages: [],
    })
    const onOpenDesign = vi.fn()
    const onSelectSession = vi.fn()
    const onClose = vi.fn()
    render(
      <SearchModal
        token="t"
        sessions={[]}
        projects={[{ id: 1, slug: 'demo-scratch', name: 'Demo Scratch' } as never]}
        features={{ designStudio: true, workflowGraph: true }}
        onClose={onClose}
        onSelectSession={onSelectSession}
        onOpenDesign={onOpenDesign}
        onSelectProject={vi.fn()}
        onSelectView={vi.fn()}
      />,
    )
    await user.type(screen.getByLabelText(/Search chats, projects, messages/i), 'farewell')
    await waitFor(() => expect(screen.getByText(/farewell announcement/i)).toBeInTheDocument())
    expect(screen.getByText(/Demo Scratch · Design/)).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: /farewell announcement/i }))
    expect(onOpenDesign).toHaveBeenCalledTimes(1)
    expect(onOpenDesign.mock.calls[0][0]).toMatchObject({
      id: 12,
      mode: 'design',
      project_slug: 'demo-scratch',
    })
    expect(onSelectSession).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('opens ordinary chats via onSelectSession', async () => {
    const user = userEvent.setup()
    const live = baseSession({ id: 5, title: 'Plan the farewell copy' })
    searchMock.mockResolvedValue({
      projects: [],
      chats: [{ id: 5, title: 'Plan the farewell copy', mode: 'chat', project_slug: 'demo-scratch' }],
      messages: [],
    })
    const onSelectSession = vi.fn()
    const onOpenDesign = vi.fn()
    const onSelectView = vi.fn()
    render(
      <SearchModal
        token="t"
        sessions={[live]}
        projects={[]}
        features={{ designStudio: true, workflowGraph: true }}
        onClose={vi.fn()}
        onSelectSession={onSelectSession}
        onOpenDesign={onOpenDesign}
        onSelectProject={vi.fn()}
        onSelectView={onSelectView}
      />,
    )
    await user.type(screen.getByLabelText(/Search chats, projects, messages/i), 'farewell')
    await waitFor(() => expect(screen.getByText('Plan the farewell copy')).toBeInTheDocument())
    await user.click(screen.getByRole('option', { name: /Plan the farewell copy/i }))
    expect(onSelectSession).toHaveBeenCalledWith(live)
    expect(onSelectView).toHaveBeenCalledWith('chat')
    expect(onOpenDesign).not.toHaveBeenCalled()
  })
})
