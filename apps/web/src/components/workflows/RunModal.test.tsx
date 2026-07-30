import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RunModal } from './RunModal'

describe('RunModal manual intake', () => {
  it('validates required values and resolves optional defaults before execution', async () => {
    const user = userEvent.setup()
    const onRun = vi.fn().mockResolvedValue(undefined)
    render(<RunModal
      title="Campaign"
      inputs={[
        { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
        { id: 'notes', label: 'Notes', kind: 'text', required: false },
        { id: 'channel', label: 'Channel', kind: 'text', required: false, default: 'email' },
      ]}
      onCancel={vi.fn()}
      onRun={onRun}
    />)

    expect(screen.getByRole('textbox', { name: 'Channel' })).toHaveValue('email')
    await user.click(screen.getByRole('button', { name: 'Run workflow' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('“Campaign” is required.')
    expect(onRun).not.toHaveBeenCalled()

    await user.type(screen.getByRole('textbox', { name: 'Campaign' }), 'Launch week')
    await user.click(screen.getByRole('button', { name: 'Run workflow' }))

    expect(onRun).toHaveBeenCalledWith({
      campaign: 'Launch week',
      channel: 'email',
    })
  })

  it('rejects invalid number and URL values with field-specific guidance', async () => {
    const user = userEvent.setup()
    const onRun = vi.fn().mockResolvedValue(undefined)
    render(<RunModal
      title="Publish"
      inputs={[
        { id: 'count', label: 'Count', kind: 'number', required: true },
        { id: 'source', label: 'Source', kind: 'url', required: true },
      ]}
      onCancel={vi.fn()}
      onRun={onRun}
    />)

    await user.type(screen.getByRole('spinbutton', { name: 'Count' }), '12')
    await user.type(screen.getByRole('textbox', { name: 'Source' }), 'example')
    await user.click(screen.getByRole('button', { name: 'Run workflow' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Source.*complete http/i)
    expect(onRun).not.toHaveBeenCalled()
  })
})
