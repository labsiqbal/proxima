import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskWorkspace } from "./TaskWorkspace";
import { approveJob, getJob, getJobDiff } from "../api/jobs";

vi.mock("../api/jobs", () => ({
	getJob: vi.fn(),
	approveJob: vi.fn(),
	deleteJob: vi.fn(),
	getJobDiff: vi.fn(),
	rejectJob: vi.fn(),
}));
vi.mock("../components/ui/Dialog", () => ({ confirmDialog: vi.fn() }));

const job = {
	id: 42,
	project_id: 1,
	project_slug: "alpha",
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
		expect(onOpenFile).toHaveBeenCalledWith("alpha", "artifacts/report.md");
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
