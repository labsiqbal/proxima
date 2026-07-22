import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SatpamCard } from './SatpamCard'
import { approveSatpamRestart, dismissSatpamRestart } from '../../api/jobs'
import type { SatpamIntervention } from '../../types'

vi.mock('../../api/jobs', () => ({
  approveSatpamRestart: vi.fn(),
  dismissSatpamRestart: vi.fn(),
}))

const steer: SatpamIntervention = {
  id: 1, job_id: 7, node_id: null, action: 'steer', detection: 'stalled', status: 'applied',
  reason: 'No new repo changes for 2 continuation turns in a row - steered the agent.',
  created_at: '2026-07-22 10:00:00', resolved_at: null,
}
const pending: SatpamIntervention = {
  id: 2, job_id: 7, node_id: null, action: 'restart', detection: 'stalled', status: 'pending',
  reason: 'Still no progress after a corrective steer. Restarting clean would DISCARD this job\'s worktree.',
  created_at: '2026-07-22 10:20:00', resolved_at: null,
}

describe('SatpamCard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders nothing for jobs the satpam never touched', () => {
    const { container } = render(<SatpamCard token="t" jobId={7} interventions={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the intervention log so automatic actions stay auditable', () => {
    render(<SatpamCard token="t" jobId={7} interventions={[steer]} />)
    expect(screen.getByText('Watchdog log')).toBeInTheDocument()
    expect(screen.getByText(/Steered · stalled/)).toBeInTheDocument()
    // No pending restart -> no approval buttons anywhere.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('a pending repo restart is an approval card: approve calls the endpoint', async () => {
    vi.mocked(approveSatpamRestart).mockResolvedValue({ id: 7 } as never)
    const onChanged = vi.fn()
    const user = userEvent.setup()
    render(<SatpamCard token="t" jobId={7} interventions={[pending, steer]} onChanged={onChanged} />)
    expect(screen.getByText('Watchdog needs your call')).toBeInTheDocument()
    expect(screen.getByText(/DISCARD/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Restart clean/ }))
    await waitFor(() => expect(approveSatpamRestart).toHaveBeenCalledWith('t', 7, 2))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(dismissSatpamRestart).not.toHaveBeenCalled()
  })

  it('dismissing keeps the job going and calls the dismiss endpoint', async () => {
    vi.mocked(dismissSatpamRestart).mockResolvedValue({ id: 7 } as never)
    const user = userEvent.setup()
    render(<SatpamCard token="t" jobId={7} interventions={[pending]} />)
    await user.click(screen.getByRole('button', { name: /Keep going as-is/ }))
    await waitFor(() => expect(dismissSatpamRestart).toHaveBeenCalledWith('t', 7, 2))
    expect(approveSatpamRestart).not.toHaveBeenCalled()
  })

  it('surfaces a refused approval without dropping the card', async () => {
    vi.mocked(approveSatpamRestart).mockRejectedValue(new Error('the repo has uncommitted changes'))
    const user = userEvent.setup()
    render(<SatpamCard token="t" jobId={7} interventions={[pending]} />)
    await user.click(screen.getByRole('button', { name: /Restart clean/ }))
    expect(await screen.findByText(/uncommitted changes/)).toBeInTheDocument()
    expect(screen.getByText('Watchdog needs your call')).toBeInTheDocument()
  })
})
