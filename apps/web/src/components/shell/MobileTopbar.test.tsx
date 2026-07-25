import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MobileTopbar } from './MobileTopbar'

describe('MobileTopbar', () => {
  it('exposes Menu, Search, New chat, and a project switcher', async () => {
    const user = userEvent.setup()
    const onMenu = vi.fn()
    const onSearch = vi.fn()
    const onNewChat = vi.fn()
    const onSelectProject = vi.fn()
    const project = { id: 1, name: 'gnhf-e2e-projects', slug: 'gnhf-e2e-projects' } as never
    render(
      <MobileTopbar
        activeProject={project}
        projects={[project]}
        onSelectProject={onSelectProject}
        onMenu={onMenu}
        onSearch={onSearch}
        onNewChat={onNewChat}
      />,
    )
    expect(screen.getByRole('button', { name: 'Active project: gnhf-e2e-projects' })).toBeInTheDocument()
    // Chrome Back is always present; disabled without a deep stack.
    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Menu' }))
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await user.click(screen.getByRole('button', { name: 'New chat' }))
    expect(onMenu).toHaveBeenCalledTimes(1)
    expect(onSearch).toHaveBeenCalledTimes(1)
    expect(onNewChat).toHaveBeenCalledTimes(1)
  })

  it('forwards the menu button ref for focus restore', () => {
    const ref = { current: null as HTMLButtonElement | null }
    render(
      <MobileTopbar
        activeProject={null}
        onMenu={() => {}}
        onSearch={() => {}}
        onNewChat={() => {}}
        menuButtonRef={ref}
      />,
    )
    expect(ref.current).toBeInstanceOf(HTMLButtonElement)
    expect(ref.current).toHaveAttribute('aria-label', 'Menu')
  })
})
