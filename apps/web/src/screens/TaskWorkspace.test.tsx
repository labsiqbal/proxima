import "@testing-library/jest-dom/vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskWorkspace } from "./TaskWorkspace";
import { approveJob, getJob, getJobDiff } from "../api/jobs";
import type { RunEvent } from "../types";

let taskEventHandler: ((event: RunEvent) => void) | null = null;

vi.mock("../api/jobs", () => ({
	getJob: vi.fn(),
	approveJob: vi.fn(),
	deleteJob: vi.fn(),
	getJobDiff: vi.fn(),
	rejectJob: vi.fn(),
}));
vi.mock("../components/ui/Dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("../hooks/useEventStream", () => ({
	useEventStream: (
		_token: string,
		_sessionId: number | null,
		onEvent: (event: RunEvent) => void,
	) => {
		taskEventHandler = onEvent;
		return { connected: true };
	},
}));

const job = {
	id: 42,
	project_id: 1,
	project_slug: "master",
	workflow_id: null,
	session_id: 9,
	title: "Audit release",
	status: "review",
	current_step_idx: 0,
	input: { brief: "Audit the release and produce a report" },
	schedule_id: null,
	created_by: 1,
	created_at: "2026-01-01",
	updated_at: "2026-01-01",
	started_at: "2026-01-01",
	finished_at: null,
	archived_at: null,
	steps_state: [
		{
			id: "task",
			name: "Task",
			instruction: "Audit the release",
			expected_output: "",
			type: "agent",
			rules: "",
			skill_ids: [],
			review_required: false,
			status: "done",
			run_id: 5,
			output_summary: "Report completed.",
			started_at: "2026-01-01",
			finished_at: "2026-01-01",
			error: null,
			produced_artifacts: [
				{ type: "file", title: "report.md", path: "artifacts/report.md" },
			],
		},
	],
};

describe("TaskWorkspace", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		taskEventHandler = null;
		vi.mocked(getJob).mockResolvedValue(job as never);
		vi.mocked(approveJob).mockResolvedValue({
			...job,
			status: "done",
		} as never);
	});

	it("renders the durable task brief, output, and deliverables", async () => {
		const onOpenFile = vi.fn();
		render(
			<TaskWorkspace
				token="token"
				jobId={42}
				onBack={vi.fn()}
				onOpenFile={onOpenFile}
			/>,
		);
		expect(await screen.findByText("Audit release")).toBeInTheDocument();
		expect(
			screen.getByText("Audit the release and produce a report"),
		).toBeInTheDocument();
		expect(screen.getByText("Report completed.")).toBeInTheDocument();
		await userEvent.click(screen.getByRole("button", { name: /report.md/ }));
		expect(onOpenFile).toHaveBeenCalledWith("master", "artifacts/report.md");
	});

	it("approves final review from the task workspace", async () => {
		const user = userEvent.setup();
		render(<TaskWorkspace token="token" jobId={42} onBack={vi.fn()} />);
		await user.click(
			await screen.findByRole("button", { name: /Approve.*Done/ }),
		);
		await waitFor(() =>
			expect(approveJob).toHaveBeenCalledWith("token", 42, undefined),
		);
	});

	it.each([
		{
			name: "inline Attention approval",
			initialStatus: "review",
			nextStatus: "done",
			eventStatus: "done",
		},
		{
			name: "checkpoint restore",
			initialStatus: "failed",
			nextStatus: "queued",
			eventStatus: "queued",
		},
	])(
		"reconciles a mounted Task after $name",
		async ({ initialStatus, nextStatus, eventStatus }) => {
			vi.mocked(getJob)
				.mockResolvedValueOnce({
					...job,
					status: initialStatus,
					steps_state: [
						{ ...job.steps_state[0], status: initialStatus },
					],
				} as never)
				.mockResolvedValue({
					...job,
					status: nextStatus,
					steps_state: [
						{
							...job.steps_state[0],
							status: nextStatus === "queued" ? "pending" : nextStatus,
						},
					],
				} as never);

			render(<TaskWorkspace token="token" jobId={42} onBack={vi.fn()} />);

			expect(
				(await screen.findAllByText(initialStatus)).length,
			).toBeGreaterThan(0);
			await act(async () => {
				taskEventHandler?.({
					id: 91,
					run_id: 0,
					session_id: 9,
					project_id: 1,
					seq: 1,
					type: "job.update",
					payload: { job_id: 42, status: eventStatus },
					created_at: "2026-01-02",
				});
			});

			await waitFor(() => expect(getJob).toHaveBeenCalledTimes(2));
			expect(screen.queryByText(initialStatus)).not.toBeInTheDocument();
			expect(screen.getAllByText(nextStatus).length).toBeGreaterThan(0);
		},
	);

	it("shows the durable prerequisite reason for a blocked queued task", async () => {
		vi.mocked(getJob).mockResolvedValue({
			...job,
			status: "queued",
			blocked_reason:
				"Waiting for prerequisite Task #7 (Research) to reach done; currently running",
			steps_state: [{ ...job.steps_state[0], status: "queued" }],
		} as never);

		render(<TaskWorkspace token="token" jobId={42} onBack={vi.fn()} />);

		expect(
			await screen.findByText(/Waiting for prerequisite Task #7/),
		).toBeInTheDocument();
	});

	it("repo job at final review: the verdict lives with the changes (slice 4)", async () => {
		const repoJob = {
			...job,
			worktree: {
				area_id: 1,
				branch: "proxima/job-42",
				base_branch: "main",
				base_commit: "aaaaaaa",
				status: "active",
				merge_commit: null,
				error: null,
				worktree_path: "/ws/worktrees/job-42",
			},
		};
		vi.mocked(getJob).mockResolvedValue(repoJob as never);
		vi.mocked(getJobDiff).mockResolvedValue({
			job_id: 42,
			branch: "proxima/job-42",
			base_branch: "main",
			worktree_status: "active",
			base_commit: "aaaaaaa",
			head_commit: "bbbbbbb",
			files: [{ path: "app.py", old_path: null, status: "A" }],
			patch: [
				"diff --git a/app.py b/app.py",
				"--- /dev/null",
				"+++ b/app.py",
				"@@ -0,0 +1 @@",
				"+x = 1",
			].join("\n"),
			patch_truncated: false,
			summary: "1 file changed, 1 insertion(+)",
		} as never);
		const user = userEvent.setup();
		render(<TaskWorkspace token="token" jobId={42} onBack={vi.fn()} />);

		// The generic bar points at the changes; the single approve door is the merge.
		expect(
			await screen.findByText(/check the changes below/),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /Approve.*Done/ }),
		).not.toBeInTheDocument();
		expect((await screen.findAllByText("app.py")).length).toBeGreaterThan(0);
		expect(screen.getByText("+x = 1")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /Approve & merge changes/ }),
		);
		await waitFor(() => expect(approveJob).toHaveBeenCalledWith("token", 42));
	});
});
