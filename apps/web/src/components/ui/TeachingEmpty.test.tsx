import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TeachingEmpty } from './TeachingEmpty'

describe('TeachingEmpty', () => {
  it('renders title, lead, capabilities, tutorial steps, and one CTA', async () => {
    const user = userEvent.setup()
    const onCta = vi.fn()
    render(
      <TeachingEmpty
        title="Start a conversation"
        lead="Hands-on work with one agent in the active project."
        capabilities={['Send prompts', 'Watch tools', 'Open deliverables']}
        steps={['Type a message and press Send', 'Review output in the thread', 'Find deliverables in Archive']}
        ctaLabel="Focus composer"
        onCta={onCta}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Start a conversation' })).toBeInTheDocument()
    expect(screen.getByText(/Hands-on work/)).toBeInTheDocument()
    expect(screen.getByLabelText('What you can do here')).toBeInTheDocument()
    expect(screen.getByText('Send prompts')).toBeInTheDocument()
    expect(screen.getByLabelText('Getting started')).toBeInTheDocument()
    expect(screen.getByText(/Type a message/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Focus composer' }))
    expect(onCta).toHaveBeenCalledOnce()
  })
})
