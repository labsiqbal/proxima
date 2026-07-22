import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChangesReview } from './ChangesReview'
import { getJobDiff, rejectJob } from '../../api/jobs'
import type { JobWorktree } from '../../types'

vi.mock('../../api/jobs', () => ({
  getJobDiff: vi.fn(),
  rejectJob: vi.fn(),
}))

const worktree: JobWorktree = {
  area_id: 1,
  branch: 'proxima/job-7',
  base_branch: 'main',
  base_commit: 'aaaaaaa',
  status: 'active',
  merge_commit: null,
  error: null,
  worktree_path: '/ws/worktrees/job-7',
}

const diff = {
  job_id: 7,
  branch: 'proxima/job-7',
  base_branch: 'main',
  worktree_status: 'active',
  base_commit: 'aaaaaaa',
  head_commit: 'bbbbbbb',
  files: [{ path: 'src/app.py', old_path: null, status: 'M' }],
  patch: [
    'diff --git a/src/app.py b/src/app.py',
    '--- a/src/app.py',
    '+++ b/src/app.py',
    '@@ -1 +1 @@',
    '-x = 1',
    '+x = 2',
  ].join('\n'),
  patch_truncated: false,
  summary: '1 file changed, 1 insertion(+), 1 deletion(-)',
}

describe('ChangesReview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getJobDiff).mockResolvedValue(diff as never)
  })

  it('shows the per-file list, the change, and both verdict doors at review', async () => {
    render(<ChangesReview
      token="token" jobId={7} jobStatus="review" worktree={worktree}
      canDecide onApprove={vi.fn()} onChanged={vi.fn()}
    />)
    // The path shows in the per-file list AND as the change's heading.
    expect(await screen.findAllByText('src/app.py')).toHaveLength(2)
    expect(screen.getByText('changed')).toBeInTheDocument()
    expect(screen.getByText('+x = 2')).toBeInTheDocument()
    expect(screen.getByText(/1 file changed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve & merge changes/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Reject…/ })).toBeInTheDocument()
    // Plain words on the surface, not git nouns.
    expect(screen.getByText(/isolated copy/)).toBeInTheDocument()
  })

  it('approve invokes the engine-specific action and refreshes the parent', async () => {
    const onApprove = vi.fn().mockResolvedValue({})
    const onChanged = vi.fn()
    const user = userEvent.setup()
    render(<ChangesReview
      token="token" jobId={7} jobStatus="review" worktree={worktree}
      canDecide onApprove={onApprove} onChanged={onChanged}
    />)
    await user.click(await screen.findByRole('button', { name: /Approve & merge changes/ }))
    await waitFor(() => expect(onApprove).toHaveBeenCalled())
    expect(onChanged).toHaveBeenCalled()
  })

  it('reject demands a reason before it can discard', async () => {
    vi.mocked(rejectJob).mockResolvedValue({} as never)
    const onChanged = vi.fn()
    const user = userEvent.setup()
    render(<ChangesReview
      token="token" jobId={7} jobStatus="review" worktree={worktree}
      canDecide onApprove={vi.fn()} onChanged={onChanged}
    />)
    await user.click(await screen.findByRole('button', { name: /Reject…/ }))
    const confirm = screen.getByRole('button', { name: /Reject & discard changes/ })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Rejection reason'), 'wrong module entirely')
    expect(confirm).toBeEnabled()
    await user.click(confirm)
    await waitFor(() => expect(rejectJob).toHaveBeenCalledWith('token', 7, 'wrong module entirely'))
    expect(onChanged).toHaveBeenCalled()
  })

  it('surfaces a merge clash plainly and offers a retry', async () => {
    render(<ChangesReview
      token="token" jobId={7} jobStatus="review"
      worktree={{ ...worktree, status: 'conflict', error: 'merge conflicts with the current main: src/app.py' }}
      canDecide onApprove={vi.fn()} onChanged={vi.fn()}
    />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be brought in/)
    expect(screen.getByText(/merge conflicts with the current main/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing in your project was changed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve again/ })).toBeInTheDocument()
  })

  it('shows the merge result after the changes landed', async () => {
    render(<ChangesReview
      token="token" jobId={7} jobStatus="done"
      worktree={{ ...worktree, status: 'merged', merge_commit: 'cafebabe123' }}
      canDecide={false} onApprove={vi.fn()} onChanged={vi.fn()}
    />)
    expect(await screen.findByText(/Changes merged into/)).toBeInTheDocument()
    expect(screen.getByText('cafebab')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
  })

  it('shows the recorded reason after a rejection, without fetching a dead diff', () => {
    render(<ChangesReview
      token="token" jobId={7} jobStatus="failed"
      worktree={{ ...worktree, status: 'discarded' }}
      rejectedReason="not needed anymore"
      canDecide={false} onApprove={vi.fn()} onChanged={vi.fn()}
    />)
    expect(screen.getByText(/Changes discarded/)).toHaveTextContent('not needed anymore')
    expect(getJobDiff).not.toHaveBeenCalled()
  })

  it('holds the verdict when the plan still has unapproved jobs', async () => {
    render(<ChangesReview
      token="token" jobId={7} jobStatus="review" worktree={worktree}
      canDecide={false} decideBlockedNote="Some jobs in this plan still need their own review — open the plan to approve them first."
      onApprove={vi.fn()} onChanged={vi.fn()}
    />)
    expect(await screen.findByText(/still need their own review/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve & merge changes/ })).not.toBeInTheDocument()
  })
})
