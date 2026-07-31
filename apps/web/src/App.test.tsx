import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	createAndStartOpsTask,
	projectSelectNavigatesToChat,
	recentSessionForProject,
	resolveArtifactReviewTarget,
	shellModeFromSearch,
	isDelegateDestination,
} from "./App";
import type { ChatSession } from "./types";
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
	projectSlug: "master",
	profileId: 7,
	executionPolicy: "guarded" as const,
};

const chatSession = (id: number, title: string): ChatSession => ({
	id,
	title,
	runner_id: "claude-code",
	project_slug: "master",
	visibility: "private",
});

describe("Shell project selection", () => {
	const enabled = () => true;
	const master: ChatSession = {
		id: 1,
		title: "Master chat",
		runner_id: "claude-code",
		project_slug: "master",
		visibility: "private",
		updated_at: "2026-01-01T10:00:00Z",
	};
	const masterNewer: ChatSession = {
		...master,
		id: 2,
		title: "Master newer",
		updated_at: "2026-01-02T10:00:00Z",
	};
	const beta: ChatSession = {
		id: 3,
		title: "Beta chat",
		runner_id: "claude-code",
		project_slug: "beta",
		visibility: "private",
		updated_at: "2026-01-03T10:00:00Z",
	};

	it("picks the most recent enabled session for the project", () => {
		expect(recentSessionForProject([master, masterNewer, beta], "master", enabled)).toEqual(masterNewer);
		expect(recentSessionForProject([master, beta], "beta", enabled)?.id).toBe(3);
	});

	it("returns null when the project has no sessions or slug is empty", () => {
		expect(recentSessionForProject([master], "missing", enabled)).toBeNull();
		expect(recentSessionForProject([master], null, enabled)).toBeNull();
		expect(recentSessionForProject([master], undefined, enabled)).toBeNull();
	});

	it("respects sessionEnabled so disabled kinds do not become active chat", () => {
		const design = { ...masterNewer, mode: "design" };
		expect(recentSessionForProject([master, design], "master", s => s.mode !== "design")).toEqual(master);
	});

	it("header shell-only mode does not navigate to chat; open-chat mode does", () => {
		expect(projectSelectNavigatesToChat("shell-only")).toBe(false);
		expect(projectSelectNavigatesToChat("open-chat")).toBe(true);
	});

	it("restores the durable mode from the URL and defaults stale values to Work", () => {
		expect(shellModeFromSearch("?mode=delegate")).toBe("delegate");
		expect(shellModeFromSearch("?mode=work")).toBe("work");
		expect(shellModeFromSearch("?mode=obsolete")).toBe("work");
	});

	it("keeps only Master and global review destinations in Delegate", () => {
		expect(isDelegateDestination("master")).toBe(true);
		expect(isDelegateDestination("activity")).toBe(true);
		expect(isDelegateDestination("artifacts")).toBe(true);
		expect(isDelegateDestination("task")).toBe(true);
		expect(isDelegateDestination("chat")).toBe(false);
		expect(isDelegateDestination("workflows")).toBe(false);
		expect(isDelegateDestination("design")).toBe(false);
	});
});

describe("Artifact review session handoff", () => {
	it("loads the exact producing session when it is omitted from the sidebar", async () => {
		const producer = chatSession(7, "Hidden producer");
		const unrelated = chatSession(9, "Unrelated active chat");
		const loadSession = vi.fn().mockResolvedValue(producer);

		await expect(resolveArtifactReviewTarget({
			sessions: [unrelated],
			sessionId: producer.id,
			fallback: unrelated,
			loadSession,
			projects: [{ slug: "master", name: "Master" }],
		})).resolves.toEqual({
			ok: true,
			session: producer,
			project: { slug: "master", name: "Master" },
		});
		expect(loadSession).toHaveBeenCalledWith(producer.id);
	});

	it("uses the opening chat only when the artifact has no producing session", async () => {
		const openingChat = chatSession(9, "Opening chat");
		const loadSession = vi.fn();

		await expect(resolveArtifactReviewTarget({
			sessions: [],
			sessionId: null,
			fallback: openingChat,
			loadSession,
			projects: [{ slug: "master", name: "Master" }],
		})).resolves.toMatchObject({ ok: true, session: openingChat });
		expect(loadSession).not.toHaveBeenCalled();
	});

	it("resolves a producing chat in another available project", async () => {
		const producer = { ...chatSession(7, "Client producer"), project_slug: "client" };
		const project = { slug: "client", name: "Client" };

		await expect(resolveArtifactReviewTarget({
			sessions: [producer],
			sessionId: producer.id,
			fallback: chatSession(9, "Current chat"),
			loadSession: vi.fn(),
			projects: [{ slug: "master", name: "Master" }, project],
		})).resolves.toEqual({ ok: true, session: producer, project });
	});

	it("fails safely when the producer session no longer exists", async () => {
		await expect(resolveArtifactReviewTarget({
			sessions: [],
			sessionId: 404,
			fallback: chatSession(9, "Current chat"),
			loadSession: vi.fn().mockRejectedValue(new Error("not found")),
			projects: [{ slug: "master", name: "Master" }],
		})).resolves.toEqual({
			ok: false,
			message: "The chat that produced this artifact is no longer available.",
		});
	});

	it("fails safely when the producer is not an available chat surface", async () => {
		const producer = { ...chatSession(7, "Design producer"), mode: "design" };

		await expect(resolveArtifactReviewTarget({
			sessions: [producer],
			sessionId: producer.id,
			fallback: null,
			loadSession: vi.fn(),
			projects: [{ slug: "master", name: "Master" }],
		})).resolves.toEqual({
			ok: false,
			message: "The chat that produced this artifact is no longer available.",
		});
	});

	it("fails safely when the producing project is unavailable", async () => {
		const producer = { ...chatSession(7, "Removed project producer"), project_slug: "removed" };

		await expect(resolveArtifactReviewTarget({
			sessions: [producer],
			sessionId: producer.id,
			fallback: null,
			loadSession: vi.fn(),
			projects: [{ slug: "master", name: "Master" }],
		})).resolves.toEqual({
			ok: false,
			message: "The project that owns this artifact's chat is no longer available.",
		});
	});
});

describe("Ops task API flow", () => {
	beforeEach(() => vi.clearAllMocks());

	it("creates and starts an ordinary durable task", async () => {
		vi.mocked(createJob).mockResolvedValue({ id: 42, session_id: 9 } as never);
		vi.mocked(startJob).mockResolvedValue({ id: 42 } as never);
		await expect(createAndStartOpsTask("token", request)).resolves.toBe(42);
		expect(createJob).toHaveBeenCalledWith("token", {
			project_slug: "master",
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
				projectSlug: "master",
				profileId: 7,
				executionPolicy: "guarded",
			}),
		).resolves.toBe(43);
		expect(createRun).toHaveBeenCalledWith("token", 10, {
			message: "/image cinematic launch poster",
			profile_id: 7,
			project_slug: "master",
		});
		expect(linkJobRun).toHaveBeenCalledWith("token", 43, 91);
		expect(startJob).not.toHaveBeenCalled();
	});

	it("rejects an underspecified media task before creating a billable run", async () => {
		await expect(
			createAndStartOpsTask("token", {
				brief: "/design poster",
				projectSlug: "master",
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
