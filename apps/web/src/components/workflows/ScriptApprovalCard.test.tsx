import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ScriptApprovalCard } from './ScriptApprovalCard'
import { getGraphNodeScript } from '../../api/graph'

vi.mock('../../api/graph', () => ({
  getGraphNodeScript: vi.fn(),
}))

const SHA = 'a'.repeat(64)
const script = {
  script: 'scripts/deploy.sh',
  sha256: SHA,
  content: '# Description: deploy\necho deploying\n',
  truncated: false,
  trusted_sha256: null,
}

describe('ScriptApprovalCard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the script content and sha256 — the owner approves bytes, not a filename', async () => {
    vi.mocked(getGraphNodeScript).mockResolvedValue(script)
    render(<ScriptApprovalCard token="t" jobId={7} nodeId="run" command="deploy.sh" approving={false} disabled={false} onApprove={vi.fn()} />)
    expect(await screen.findByText(/echo deploying/)).toBeInTheDocument()
    expect(screen.getByText(SHA)).toBeInTheDocument()
    expect(getGraphNodeScript).toHaveBeenCalledWith('t', 7, 'run')
  })

  it('approving hands back the sha256 that was reviewed', async () => {
    vi.mocked(getGraphNodeScript).mockResolvedValue(script)
    const onApprove = vi.fn()
    const user = userEvent.setup()
    render(<ScriptApprovalCard token="t" jobId={7} nodeId="run" command="deploy.sh" approving={false} disabled={false} onApprove={onApprove} />)
    await screen.findByText(/echo deploying/)
    await user.click(screen.getByRole('button', { name: /Approve script & run/ }))
    expect(onApprove).toHaveBeenCalledWith(SHA)
  })

  it('cannot approve before the content has loaded, or when it failed to load', async () => {
    vi.mocked(getGraphNodeScript).mockRejectedValue(new Error('script vanished'))
    const onApprove = vi.fn()
    render(<ScriptApprovalCard token="t" jobId={7} nodeId="run" command="deploy.sh" approving={false} disabled={false} onApprove={onApprove} />)
    expect(screen.getByRole('button', { name: /Approve script & run/ })).toBeDisabled()
    expect(await screen.findByText(/script vanished/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve script & run/ })).toBeDisabled()
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('flags a script that changed since its last approved version', async () => {
    vi.mocked(getGraphNodeScript).mockResolvedValue({ ...script, trusted_sha256: 'b'.repeat(64) })
    render(<ScriptApprovalCard token="t" jobId={7} nodeId="run" command="deploy.sh" approving={false} disabled={false} onApprove={vi.fn()} />)
    expect(await screen.findByText(/changed since the last approved version/)).toBeInTheDocument()
  })
})
