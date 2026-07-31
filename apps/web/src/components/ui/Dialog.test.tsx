import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { confirmDialog, DialogHost } from './Dialog'

describe('DialogHost', () => {
  it('traps focus, closes with Escape, and restores the trigger', async () => {
    render(<>
      <button type="button" onClick={() => {
        void confirmDialog({
          title: 'This chat already has an unsent draft',
          message: 'Append the artifact feedback to preserve both drafts.',
          confirmLabel: 'Append feedback',
          cancelLabel: 'Keep current draft',
        })
      }}>Open conflict</button>
      <DialogHost />
    </>)

    const trigger = screen.getByRole('button', { name: 'Open conflict' })
    await userEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'This chat already has an unsent draft' })
    const cancel = screen.getByRole('button', { name: 'Keep current draft' })
    const accept = screen.getByRole('button', { name: 'Append feedback' })

    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleDescription('Append the artifact feedback to preserve both drafts.')
    await waitFor(() => expect(cancel).toHaveFocus())
    accept.focus()
    await userEvent.tab()
    expect(cancel).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(accept).toHaveFocus()

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })
})
