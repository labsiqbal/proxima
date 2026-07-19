import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomeScreen } from "./HomeScreen";
import { getDashboard } from "../api/dashboard";
import { listReferenceFiles } from "../api/files";

vi.mock("../api/dashboard", () => ({ getDashboard: vi.fn() }));
vi.mock("../api/commands", () => ({
	getCommandCatalog: vi.fn().mockResolvedValue({ groups: [] }),
}));
vi.mock("../api/files", () => ({
	listReferenceFiles: vi.fn(),
	uploadFile: vi.fn(),
}));

const project = {
	slug: "alpha",
	name: "Alpha",
	path: "/tmp/alpha",
	owner: "owner",
	role: "owner",
	visibility: "private" as const,
};
const profile = {
	id: 7,
	name: "Builder",
	slug: "builder",
	runner_id: "codex",
	default_model: null,
	is_default: true,
};
const dashboard = {
	counts: { projects: 1, chats: 0 },
	jobsByStatus: { queued: 0, running: 0, review: 0, done: 0 },
	recent: [],
	activeSessions: [],
	projects: [{ slug: "alpha", name: "Alpha", chats: 0 }],
	workflows: [],
	schedules: [],
	reviewCount: 0,
	reviewJobs: [],
	recentArtifacts: [],
	pendingApprovals: [],
	authHealth: { status: "ready", checks: [] },
	systemHealth: {
		runnersReady: 1,
		runnersTotal: 1,
		failedRuns24h: 0,
		staleRuns: 0,
	},
};
const base = {
	token: "token",
	features: { designStudio: false, workflowGraph: false },
	projects: [project],
	activeProject: project,
	activeProfile: profile,
	profiles: [profile],
	onActiveProject: vi.fn(),
	onActiveProfile: vi.fn(),
	onCreateTask: vi.fn().mockResolvedValue(41),
	onOpenJob: vi.fn(),
	onSelectView: vi.fn(),
};

describe("HomeScreen Ops task composer", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.mocked(getDashboard).mockResolvedValue(dashboard as never);
		vi.mocked(listReferenceFiles).mockResolvedValue({
			files: [{ path: "docs/release-brief.md" }],
			truncated: false,
		});
	});

	it("submits a guarded project-scoped task", async () => {
		const user = userEvent.setup();
		render(<HomeScreen {...base} />);
		await screen.findByRole("heading", {
			name: "What should Ops take care of?",
		});
		const submit = screen.getByRole("button", { name: "Start task" });
		expect(submit).toBeDisabled();
		await user.type(
			screen.getByRole("textbox", { name: "Task brief" }),
			"  Audit the release  ",
		);
		await user.click(submit);
		await waitFor(() =>
			expect(base.onCreateTask).toHaveBeenCalledWith({
				brief: "Audit the release",
				projectSlug: "alpha",
				profileId: 7,
				executionPolicy: "guarded",
			}),
		);
		expect(base.onOpenJob).toHaveBeenCalledWith(41);
	});

	it("submits a project file selected through @ as part of the task brief", async () => {
		const user = userEvent.setup();
		render(<HomeScreen {...base} />);
		const brief = await screen.findByRole("textbox", { name: "Task brief" });
		await user.type(brief, "Audit @release");
		expect(await screen.findByText("docs/release-brief.md")).toBeInTheDocument();
		await user.keyboard("{Enter}");
		expect(base.onCreateTask).not.toHaveBeenCalled();
		await user.type(brief, "for launch");
		await user.click(screen.getByRole("button", { name: "Start task" }));

		await waitFor(() =>
			expect(base.onCreateTask).toHaveBeenCalledWith({
				brief: "Audit docs/release-brief.md for launch",
				projectSlug: "alpha",
				profileId: 7,
				executionPolicy: "guarded",
			}),
		);
	});

	it("does not navigate when task creation resolves after Home unmounts", async () => {
		const user = userEvent.setup();
		let resolveTask!: (jobId: number) => void;
		const onCreateTask = vi.fn(
			() =>
				new Promise<number>((resolve) => {
					resolveTask = resolve;
				}),
		);
		const onOpenJob = vi.fn();
		const view = render(
			<HomeScreen
				{...base}
				onCreateTask={onCreateTask}
				onOpenJob={onOpenJob}
			/>,
		);
		await user.type(
			await screen.findByRole("textbox", { name: "Task brief" }),
			"Deferred task",
		);
		await user.click(screen.getByRole("button", { name: "Start task" }));
		await waitFor(() => expect(onCreateTask).toHaveBeenCalledTimes(1));
		view.unmount();
		resolveTask(77);
		await Promise.resolve();
		expect(onOpenJob).not.toHaveBeenCalled();
	});

	it("keeps the brief and announces task failure", async () => {
		const user = userEvent.setup();
		render(
			<HomeScreen
				{...base}
				onCreateTask={vi.fn().mockRejectedValue(new Error("start failed"))}
			/>,
		);
		await user.type(
			await screen.findByRole("textbox", { name: "Task brief" }),
			"Keep this text",
		);
		await user.click(screen.getByRole("button", { name: "Start task" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("start failed");
		expect(screen.getByRole("textbox", { name: "Task brief" })).toHaveValue(
			"Keep this text",
		);
	});

	it("integrates project, agent, attachments, image/design, and policy controls", async () => {
		const user = userEvent.setup();
		render(
			<HomeScreen
				{...base}
				features={{ ...base.features, designStudio: true }}
			/>,
		);
		await screen.findByRole("heading", {
			name: "What should Ops take care of?",
		});
		expect(
			screen.queryByRole("button", { name: "Normal" }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: /Alpha/ })).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Agent" })).toBeInTheDocument();
		expect(screen.getByText("Builder")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Execution policy" }),
		).toBeInTheDocument();
		expect(screen.getByText("Guarded")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Add" }));
		expect(
			screen.getByRole("menuitem", { name: /Attach files/ }),
		).toBeInTheDocument();
		expect(screen.getByRole("menuitem", { name: /Image/ })).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: /Design draft/ }),
		).toBeInTheDocument();
	});

	it("keeps destination dashboards off the launcher", async () => {
		render(<HomeScreen {...base} />);
		await screen.findByRole("heading", {
			name: "What should Ops take care of?",
		});
		expect(screen.queryByText("Scheduled")).not.toBeInTheDocument();
		expect(screen.queryByText("Deliverables")).not.toBeInTheDocument();
		expect(
			screen.queryByText("Running & recent tasks"),
		).not.toBeInTheDocument();
	});

	it("shows one compact authoritative attention strip", async () => {
		vi.mocked(getDashboard).mockResolvedValue({
			...dashboard,
			reviewCount: 1,
			reviewJobs: [
				{
					id: 88,
					title: "Older review",
					project_slug: "alpha",
					status: "review",
				},
			],
		} as never);
		render(<HomeScreen {...base} />);
		expect(
			await screen.findByText("1 task needs your attention"),
		).toBeInTheDocument();
		expect(screen.getByText("Older review")).toBeInTheDocument();
	});
});
