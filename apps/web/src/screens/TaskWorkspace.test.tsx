import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskWorkspace } from "./TaskWorkspace";
import { approveJob, getJob } from "../api/jobs";

vi.mock("../api/jobs", () => ({
	getJob: vi.fn(),
	approveJob: vi.fn(),
	deleteJob: vi.fn(),
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
});
