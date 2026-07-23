import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import React from 'react'
import { ConvertToWorkflowButton } from './ConvertToWorkflowButton'
import { promoteWorkflow } from '../api/sessions'

vi.mock('../api/sessions', () => ({
  promoteWorkflow: vi.fn(),
}))

vi.mock('../hooks/useEventStream', () => ({
  useEventStream: () => undefined,
}))

describe('ConvertToWorkflowButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('ignores a second click while the first promote is in flight', async () => {
    const user = userEvent.setup()
    let release!: (value: { run_id: number }) => void
    const gate = new Promise<{ run_id: number }>(resolve => {
      release = resolve
    })
    vi.mocked(promoteWorkflow).mockReturnValue(gate as never)

    render(
      <ConvertToWorkflowButton
        token="t"
        sessionId={7}
        onDraft={() => undefined}
      />,
    )

    const button = screen.getByRole('button', { name: 'Slice into plan' })
    await user.click(button)
    await user.click(button)
    await user.click(button)

    expect(promoteWorkflow).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Slicing into plan…' })).toBeDisabled()

    release({ run_id: 99 })
    await waitFor(() => expect(promoteWorkflow).toHaveBeenCalledTimes(1))
  })
})
