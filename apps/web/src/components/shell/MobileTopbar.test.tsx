import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MobileTopbar } from './MobileTopbar'

describe('MobileTopbar', () => {
  it('exposes Menu, Search, and New chat actions', async () => {
    const user = userEvent.setup()
    const onMenu = vi.fn()
    const onSearch = vi.fn()
    const onNewChat = vi.fn()
    render(
      <MobileTopbar
        activeProject={{ id: 1, name: 'gnhf-e2e-projects', slug: 'gnhf-e2e-projects' } as never}
        onMenu={onMenu}
        onSearch={onSearch}
        onNewChat={onNewChat}
      />,
    )
    expect(screen.getByText('gnhf-e2e-projects')).toBeInTheDocument()
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
