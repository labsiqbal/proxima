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
    // Non-destructive confirms focus the primary action: a reflexive Enter must
    // confirm, not silently dismiss the dialog (the B6 "click did nothing" trap).
    await waitFor(() => expect(accept).toHaveFocus())
    await userEvent.tab()
    expect(cancel).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(accept).toHaveFocus()

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('confirms on Enter for a non-destructive dialog instead of silently dismissing', async () => {
    let result: boolean | null = null
    render(<>
      <button type="button" onClick={() => {
        void confirmDialog({
          title: 'Run project command?',
          message: 'Proxima will run "npm run dev" with your account permissions.',
          confirmLabel: 'Run app',
        }).then(v => { result = v })
      }}>Open run</button>
      <DialogHost />
    </>)

    await userEvent.click(screen.getByRole('button', { name: 'Open run' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run app' })).toHaveFocus())
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(result).toBe(true))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps Cancel focused for destructive confirms so Enter stays safe', async () => {
    let result: boolean | null = null
    render(<>
      <button type="button" onClick={() => {
        void confirmDialog({
          title: 'Delete this task?',
          message: 'This cannot be undone.',
          confirmLabel: 'Delete',
          danger: true,
        }).then(v => { result = v })
      }}>Open delete</button>
      <DialogHost />
    </>)

    await userEvent.click(screen.getByRole('button', { name: 'Open delete' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus())
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(result).toBe(false))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
