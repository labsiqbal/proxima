import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowInputsEditor } from './SaveTemplateModal'

describe('WorkflowInputsEditor', () => {
  it('creates and deletes complete rows atomically with stable generated IDs', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    function Harness() {
      const [inputs, setInputs] = useState<Array<{
        id: string
        label: string
        kind: 'text'
        required: boolean
      }>>([])
      return <WorkflowInputsEditor
        inputs={inputs}
        onChange={next => {
          setInputs(next as typeof inputs)
          onChange(next)
        }}
      />
    }
    render(<Harness />)

    await user.click(screen.getByRole('button', { name: '+ Add field' }))

    expect(onChange).toHaveBeenLastCalledWith([
      { id: 'field', label: 'New field', kind: 'text', required: false },
    ])
    expect(onChange.mock.calls.flat().some(call =>
      Array.isArray(call) && call.some(item => !item.id || !item.label),
    )).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Remove input' }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })

  it('stages ID edits, exposes duplicate and invalid errors, then commits one valid row', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const onEditStateChange = vi.fn()
    const initial = [
        { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
        { id: 'audience', label: 'Audience', kind: 'text', required: false },
    ] as const
    function Harness() {
      const [inputs, setInputs] = useState([...initial])
      return <WorkflowInputsEditor
        inputs={inputs}
        onChange={next => {
          setInputs(next)
          onChange(next)
        }}
        onEditStateChange={onEditStateChange}
      />
    }
    render(<Harness />)

    const audienceId = screen.getByRole('textbox', { name: 'Input 2 ID' })
    await user.clear(audienceId)
    await user.type(audienceId, 'campaign')
    await user.tab()

    expect(screen.getByText('ID must be unique.')).toBeInTheDocument()
    expect(audienceId).toHaveAttribute('aria-invalid', 'true')
    expect(onChange).not.toHaveBeenCalled()
    expect(onEditStateChange).toHaveBeenLastCalledWith({
      dirty: true,
      valid: false,
    })

    await user.clear(audienceId)
    await user.type(audienceId, 'audience_2')
    await user.tab()

    expect(screen.queryByText('ID must be unique.')).not.toBeInTheDocument()
    expect(onChange).toHaveBeenLastCalledWith([
      { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
      { id: 'audience_2', label: 'Audience', kind: 'text', required: false },
    ])
    expect(onEditStateChange).toHaveBeenLastCalledWith({
      dirty: false,
      valid: true,
    })
  })

  it('discards a staged valid ID edit on Escape without changing the stable row', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<WorkflowInputsEditor
      inputs={[{ id: 'campaign', label: 'Campaign', kind: 'text', required: true }]}
      onChange={onChange}
    />)

    const id = screen.getByRole('textbox', { name: 'Input 1 ID' })
    await user.clear(id)
    await user.type(id, 'renamed')
    await user.keyboard('{Escape}')

    expect(id).toHaveValue('campaign')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('stages rapid label and default edits until each complete row can autosave', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<WorkflowInputsEditor
      inputs={[{ id: 'count', label: 'Count', kind: 'number', required: false }]}
      onChange={onChange}
    />)

    const label = screen.getByRole('textbox', { name: 'Input 1 label' })
    await user.clear(label)
    await user.type(label, 'Item count')
    await user.tab()
    const defaultValue = screen.getByRole('spinbutton', { name: 'Input 1 default' })
    await user.type(defaultValue, '12')
    await user.tab()

    expect(onChange).toHaveBeenLastCalledWith([
      {
        id: 'count',
        label: 'Item count',
        kind: 'number',
        required: false,
        default: '12',
      },
    ])
  })
})
