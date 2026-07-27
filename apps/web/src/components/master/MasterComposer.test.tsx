import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MasterComposer } from './MasterComposer'
import { useMasterState } from '../../master/MasterStateProvider'
import { getCommandCatalog } from '../../api/commands'
import { uploadFile, listReferenceFiles, listArtifacts } from '../../api/files'

vi.mock('../../master/MasterStateProvider', () => ({ useMasterState: vi.fn() }))
vi.mock('../../api/commands', () => ({ getCommandCatalog: vi.fn() }))
vi.mock('../../api/files', () => ({
  uploadFile: vi.fn(),
  listReferenceFiles: vi.fn(),
  listArtifacts: vi.fn(),
}))

const send = vi.fn().mockResolvedValue(undefined)

function mockState(draft: string) {
  vi.mocked(useMasterState).mockReturnValue({
    activeRun: null,
    composer: { draft, selection: { start: 0, end: 0 }, sending: false, error: '', focusRequest: 0 },
    desk: { unattended: false },
    actions: { setDraft: vi.fn(), setSelection: vi.fn(), send },
  } as never)
}

describe('MasterComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getCommandCatalog).mockReturnValue(new Promise(() => {}))
    vi.mocked(listReferenceFiles).mockReturnValue(new Promise(() => {}))
    vi.mocked(listArtifacts).mockReturnValue(new Promise(() => {}))
  })

  it('forwards the provider draft to the provider submission', async () => {
    mockState('Ship the durable turn')
    render(<MasterComposer token="token" projectSlug="master-project" />)
    fireEvent.click(screen.getByRole('button', { name: 'Send to Master' }))

    await waitFor(() => expect(send).toHaveBeenCalledTimes(1))
    expect(send).toHaveBeenCalledWith('Ship the durable turn')
  })

  it('forwards uploaded attachment references alongside the message', async () => {
    mockState('Review this')
    vi.mocked(uploadFile).mockResolvedValue({
      path: 'master-project/uploads/report.pdf',
      name: 'report.pdf',
    } as never)
    const { container } = render(
      <MasterComposer token="token" projectSlug="master-project" />,
    )
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['data'], 'report.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByText('report.pdf')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'Send to Master' }))

    await waitFor(() => expect(send).toHaveBeenCalledTimes(1))
    expect(send).toHaveBeenCalledWith(
      'Review this\n\n[report.pdf](master-project/uploads/report.pdf)',
    )
  })
})
