import { beforeEach, describe, expect, it, vi } from "vitest";
import { createAndStartOpsTask } from "./App";
import { createJob, deleteJob, linkJobRun, startJob } from "./api/jobs";
import { createRun } from "./api/runs";

vi.mock("./api/jobs", () => ({
	createJob: vi.fn(),
	startJob: vi.fn(),
	linkJobRun: vi.fn(),
	deleteJob: vi.fn(),
	getJob: vi.fn(),
	listJobs: vi.fn(),
}));
vi.mock("./api/runs", () => ({ createRun: vi.fn(), activeRuns: vi.fn() }));

const request = {
	brief: "  Audit release  ",
	projectSlug: "alpha",
	profileId: 7,
	executionPolicy: "guarded" as const,
};

describe("Ops task API flow", () => {
	beforeEach(() => vi.clearAllMocks());

	it("creates and starts an ordinary durable task", async () => {
		vi.mocked(createJob).mockResolvedValue({ id: 42, session_id: 9 } as never);
		vi.mocked(startJob).mockResolvedValue({ id: 42 } as never);
		await expect(createAndStartOpsTask("token", request)).resolves.toBe(42);
		expect(createJob).toHaveBeenCalledWith("token", {
			project_slug: "alpha",
			profile_id: 7,
			title: "Audit release",
			input: { brief: "Audit release", task_kind: "agent", execution_policy: "guarded" },
		});
		expect(startJob).toHaveBeenCalledWith("token", 42);
		expect(createRun).not.toHaveBeenCalled();
	});

	it("routes image intent through the proven media run and links it to the task", async () => {
		vi.mocked(createJob).mockResolvedValue({ id: 43, session_id: 10 } as never);
		vi.mocked(createRun).mockResolvedValue({
			run_id: 91,
			session_id: 10,
			status: "queued",
		});
		vi.mocked(linkJobRun).mockResolvedValue({ id: 43 } as never);
		await expect(
			createAndStartOpsTask("token", {
				brief: "/image cinematic launch poster",
				projectSlug: "alpha",
				profileId: 7,
				executionPolicy: "guarded",
			}),
		).resolves.toBe(43);
		expect(createRun).toHaveBeenCalledWith("token", 10, {
			message: "/image cinematic launch poster",
			profile_id: 7,
			project_slug: "alpha",
		});
		expect(linkJobRun).toHaveBeenCalledWith("token", 43, 91);
		expect(startJob).not.toHaveBeenCalled();
	});

	it("rejects an underspecified media task before creating a billable run", async () => {
		await expect(
			createAndStartOpsTask("token", {
				brief: "/design poster",
				projectSlug: "alpha",
				profileId: 7,
				executionPolicy: "guarded",
			}),
		).rejects.toThrow(/clearer design brief/i);
		expect(createJob).not.toHaveBeenCalled();
	});

	it("deletes the created task when ordinary start fails", async () => {
		const failure = new Error("runner unavailable");
		vi.mocked(createJob).mockResolvedValue({ id: 44, session_id: 11 } as never);
		vi.mocked(startJob).mockRejectedValue(failure);
		vi.mocked(deleteJob).mockResolvedValue({ ok: true });
		await expect(
			createAndStartOpsTask("token", { ...request, brief: "Audit" }),
		).rejects.toBe(failure);
		expect(deleteJob).toHaveBeenCalledWith("token", 44);
	});

	it("identifies the orphaned task when start and cleanup both fail", async () => {
		vi.mocked(createJob).mockResolvedValue({ id: 45, session_id: 12 } as never);
		vi.mocked(startJob).mockRejectedValue(new Error("start failed"));
		vi.mocked(deleteJob).mockRejectedValue(new Error("delete failed"));
		await expect(
			createAndStartOpsTask("token", { ...request, brief: "Audit" }),
		).rejects.toThrow(/delete task #45 before retrying/i);
	});
});
