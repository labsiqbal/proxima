import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	createAndStartOpsTask,
	nextFocusedWorkItemId,
	projectSelectNavigatesToChat,
	recentSessionForProject,
	resolveArtifactReviewTarget,
	resolvePreservedWorkSelection,
	resolveRoutedWorkSession,
	shellModeFromSearch,
	shouldPushFocusedItemHistory,
	isDelegateDestination,
	opsMigrationSlugFromHash,
	workRouteDesignOpenSync,
	workRouteFocusedItemIds,
	workRouteSessionId,
	planOpenMasterConversation,
	projectForShellScope,
} from "./App";
import type { ChatSession, Project } from "./types";
import { createJob, deleteJob, linkJobRun, startJob } from "./api/jobs";
import { createRun } from "./api/runs";
import { workRouteUrl } from "./lib/workRoute";
import {
	designLeaveEmptyAbort,
	designSessionKeepAliveMatches,
	designSessionOpenAbortReset,
	designSessionOpenCancelStage,
} from "./screens/DesignStudio";

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

	it("picks the most recent session for the project", () => {
		expect(recentSessionForProject([master, masterNewer, beta], "master")).toEqual(masterNewer);
		expect(recentSessionForProject([master, beta], "beta")?.id).toBe(3);
	});

	it("returns null when the project has no sessions or slug is empty", () => {
		expect(recentSessionForProject([master], "missing")).toBeNull();
		expect(recentSessionForProject([master], null)).toBeNull();
		expect(recentSessionForProject([master], undefined)).toBeNull();
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
		// Files is a Delegate destination too (ADR-0040): global trees behind
		// a head filter, like Tasks. It also carries the deliverable ledger -
		// the separate Archive destination is gone (#139).
		expect(isDelegateDestination("files")).toBe(true);
		expect(isDelegateDestination("task")).toBe(true);
		expect(isDelegateDestination("chat")).toBe(false);
		expect(isDelegateDestination("workflows")).toBe(false);
		expect(isDelegateDestination("design")).toBe(false);
	});

	it("keeps an in-app session B pick and serializes New chat without a stale session", () => {
		const sessionA = master;
		const sessionB = masterNewer;
		expect(
			resolveRoutedWorkSession({
				sessions: [sessionA, sessionB, beta],
				projectSlug: "master",
				sessionId: sessionB.id,
			}),
		).toEqual(sessionB);
		expect(
			workRouteSessionId({
				mode: "work",
				projectSlug: "master",
				activeSession: sessionB,
			}),
		).toBe(sessionB.id);
		expect(
			workRouteUrl("http://localhost/?mode=work&view=chat&project=master&session=1", {
				mode: "work",
				view: "chat",
				projectSlug: "master",
				sessionId: workRouteSessionId({
					mode: "work",
					projectSlug: "master",
					activeSession: null,
				}),
				workflowJobId: null,
				designId: null,
			}),
		).toBe("/?mode=work&view=chat&project=master");
		expect(
			resolveRoutedWorkSession({
				sessions: [sessionA, sessionB, beta],
				projectSlug: "master",
				sessionId: null,
			}),
		).toBeNull();
	});

	it("falls back inside the project only when a routed session is gone", () => {
		expect(
			resolveRoutedWorkSession({
				sessions: [master, masterNewer, beta],
				projectSlug: "master",
				sessionId: 404,
			}),
		).toEqual(masterNewer);
		expect(
			resolveRoutedWorkSession({
				sessions: [master, beta],
				projectSlug: "master",
				sessionId: beta.id,
			}),
		).toEqual(master);
	});

	it("serializes focused Workflow/Design ids before editor/studio stage readiness", () => {
		// Cold-load staging still has graphStage=home / designStage=start; the URL
		// must keep the deep identity so replaceState does not strip it and force a
		// spurious history entry when the surface later reports editor/studio.
		const workflowPending = workRouteFocusedItemIds({
			mode: "work",
			view: "workflows",
			graphItemId: 9,
			designItemId: null,
		});
		const designPending = workRouteFocusedItemIds({
			mode: "work",
			view: "design",
			graphItemId: null,
			designItemId: "launch-poster",
		});
		expect(workflowPending).toEqual({ workflowJobId: 9, designId: null });
		expect(designPending).toEqual({
			workflowJobId: null,
			designId: "launch-poster",
		});
		const coldWorkflowUrl = workRouteUrl(
			"http://localhost/?mode=work&view=workflows&project=atlas&session=42&workflow=9",
			{
				mode: "work",
				view: "workflows",
				projectSlug: "atlas",
				sessionId: 42,
				...workflowPending,
			},
		);
		const coldDesignUrl = workRouteUrl(
			"http://localhost/?mode=work&view=design&project=atlas&session=42&design=launch-poster",
			{
				mode: "work",
				view: "design",
				projectSlug: "atlas",
				sessionId: 42,
				...designPending,
			},
		);
		expect(coldWorkflowUrl).toBe(
			"/?mode=work&view=workflows&project=atlas&session=42&workflow=9",
		);
		expect(coldDesignUrl).toBe(
			"/?mode=work&view=design&project=atlas&session=42&design=launch-poster",
		);
		// Same URL before and after stage readiness ⇒ no pushState on deep restore,
		// so native Back leaves the focused entry rather than a stripped surface.
		expect(
			workRouteUrl("http://localhost/", {
				mode: "work",
				view: "workflows",
				projectSlug: "atlas",
				sessionId: 42,
				...workRouteFocusedItemIds({
					mode: "work",
					view: "workflows",
					graphItemId: 9,
					designItemId: null,
				}),
			}),
		).toBe(coldWorkflowUrl);
		expect(
			workRouteFocusedItemIds({
				mode: "work",
				view: "chat",
				graphItemId: 9,
				designItemId: "launch-poster",
			}),
		).toEqual({ workflowJobId: null, designId: null });
	});

	it("keeps deep Workflow/Design identity across mount and loading stage reports", () => {
		// Cold-load/reload sequence for workflow=9: seed id, then child mount home,
		// editor+null while loadJob runs, finally editor+9. Identity must never drop,
		// URL must stay stable, and restore must not push a history entry.
		const priorUrl =
			"/?mode=work&view=chat&project=atlas&session=42";
		const deepWorkflowUrl =
			"/?mode=work&view=workflows&project=atlas&session=42&workflow=9";
		const deepDesignUrl =
			"/?mode=work&view=design&project=atlas&session=42&design=launch-poster";

		const workflowStages: Array<{
			prev: "home" | "editor";
			next: "home" | "editor";
			id: number | null;
		}> = [
			{ prev: "home", next: "home", id: null }, // mount report
			{ prev: "home", next: "editor", id: null }, // openJob before load finishes
			{ prev: "editor", next: "editor", id: 9 }, // load settles
		];
		let graphItemId: number | null = 9;
		let historyLen = 2; // [prior chat, deep workflow]
		const workflowSnapshots: string[] = [];
		for (const step of workflowStages) {
			graphItemId = nextFocusedWorkItemId({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "editor",
				reportedId: step.id,
				currentId: graphItemId,
			});
			if (
				shouldPushFocusedItemHistory({
					nextStage: step.next,
					focusedStage: "editor",
					reportedId: step.id,
					routedId: 9,
				})
			) {
				historyLen += 1;
			}
			const url = workRouteUrl("http://localhost/", {
				mode: "work",
				view: "workflows",
				projectSlug: "atlas",
				sessionId: 42,
				...workRouteFocusedItemIds({
					mode: "work",
					view: "workflows",
					graphItemId,
					designItemId: null,
				}),
			});
			workflowSnapshots.push(url);
		}
		expect(graphItemId).toBe(9);
		expect(workflowSnapshots.every(url => url === deepWorkflowUrl)).toBe(true);
		expect(historyLen).toBe(2);

		const designStages: Array<{
			prev: "start" | "studio";
			next: "start" | "studio";
			id: string | null;
		}> = [
			{ prev: "start", next: "start", id: null },
			{ prev: "start", next: "studio", id: null },
			{ prev: "studio", next: "studio", id: "launch-poster" },
		];
		let designItemId: string | null = "launch-poster";
		let designHistoryLen = 2;
		const designSnapshots: string[] = [];
		for (const step of designStages) {
			designItemId = nextFocusedWorkItemId({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "studio",
				reportedId: step.id,
				currentId: designItemId,
			});
			if (
				shouldPushFocusedItemHistory({
					nextStage: step.next,
					focusedStage: "studio",
					reportedId: step.id,
					routedId: "launch-poster",
				})
			) {
				designHistoryLen += 1;
			}
			const url = workRouteUrl("http://localhost/", {
				mode: "work",
				view: "design",
				projectSlug: "atlas",
				sessionId: 42,
				...workRouteFocusedItemIds({
					mode: "work",
					view: "design",
					graphItemId: null,
					designItemId,
				}),
			});
			designSnapshots.push(url);
		}
		expect(designItemId).toBe("launch-poster");
		expect(designSnapshots.every(url => url === deepDesignUrl)).toBe(true);
		expect(designHistoryLen).toBe(2);

		// Real leave from focused stage clears identity; native Back returns to prior.
		expect(
			nextFocusedWorkItemId({
				prevStage: "editor",
				nextStage: "home",
				focusedStage: "editor",
				reportedId: null,
				currentId: 9,
			}),
		).toBeNull();
		expect(
			nextFocusedWorkItemId({
				prevStage: "studio",
				nextStage: "start",
				focusedStage: "studio",
				reportedId: null,
				currentId: "launch-poster",
			}),
		).toBeNull();
		expect(priorUrl).toBe(
			"/?mode=work&view=chat&project=atlas&session=42",
		);
		// In-app open of a different item still pushes once the real id arrives.
		expect(
			shouldPushFocusedItemHistory({
				nextStage: "editor",
				focusedStage: "editor",
				reportedId: 10,
				routedId: 9,
			}),
		).toBe(true);
		expect(
			shouldPushFocusedItemHistory({
				nextStage: "editor",
				focusedStage: "editor",
				reportedId: null,
				routedId: 9,
			}),
		).toBe(false);
	});

	it("retargets Workflow A→home→B without flashing A or extra history", () => {
		// openJob publishes the target id immediately; keep-alive job A must never
		// re-enter the URL or history while B is loading.
		const homeUrl = "/?mode=work&view=workflows&project=atlas&session=42";
		const bUrl =
			"/?mode=work&view=workflows&project=atlas&session=42&workflow=22";
		const steps: Array<{
			prev: "home" | "editor";
			next: "home" | "editor";
			id: number | null;
		}> = [
			{ prev: "editor", next: "home", id: null }, // leave A
			{ prev: "home", next: "editor", id: 22 }, // open B reports target immediately
			{ prev: "editor", next: "editor", id: 22 }, // load settles
		];
		let graphItemId: number | null = 11;
		let routedId: number | null = 11;
		let historyLen = 2; // [prior, A]
		const snapshots: string[] = [];
		for (const step of steps) {
			if (
				shouldPushFocusedItemHistory({
					nextStage: step.next,
					focusedStage: "editor",
					reportedId: step.id,
					routedId,
				})
			) {
				historyLen += 1;
			}
			graphItemId = nextFocusedWorkItemId({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "editor",
				reportedId: step.id,
				currentId: graphItemId,
			});
			const url = workRouteUrl("http://localhost/", {
				mode: "work",
				view: "workflows",
				projectSlug: "atlas",
				sessionId: 42,
				...workRouteFocusedItemIds({
					mode: "work",
					view: "workflows",
					graphItemId,
					designItemId: null,
				}),
			});
			snapshots.push(url);
			routedId = graphItemId;
		}
		expect(snapshots[0]).toBe(homeUrl);
		expect(snapshots.slice(1).every(url => url === bUrl)).toBe(true);
		expect(snapshots.some(url => url.includes("workflow=11"))).toBe(false);
		expect(graphItemId).toBe(22);
		// Exactly one history push for B after leaving A.
		expect(historyLen).toBe(3);

		// Design shares the same open-target report shape.
		const designHome = "/?mode=work&view=design&project=atlas&session=42";
		const designB =
			"/?mode=work&view=design&project=atlas&session=42&design=poster-b";
		const designSteps: Array<{
			prev: "start" | "studio";
			next: "start" | "studio";
			id: string | null;
		}> = [
			{ prev: "studio", next: "start", id: null },
			{ prev: "start", next: "studio", id: "poster-b" },
			{ prev: "studio", next: "studio", id: "poster-b" },
		];
		let designItemId: string | null = "poster-a";
		let designRouted: string | null = "poster-a";
		let designHistoryLen = 2;
		const designSnapshots: string[] = [];
		for (const step of designSteps) {
			if (
				shouldPushFocusedItemHistory({
					nextStage: step.next,
					focusedStage: "studio",
					reportedId: step.id,
					routedId: designRouted,
				})
			) {
				designHistoryLen += 1;
			}
			designItemId = nextFocusedWorkItemId({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "studio",
				reportedId: step.id,
				currentId: designItemId,
			});
			const url = workRouteUrl("http://localhost/", {
				mode: "work",
				view: "design",
				projectSlug: "atlas",
				sessionId: 42,
				...workRouteFocusedItemIds({
					mode: "work",
					view: "design",
					graphItemId: null,
					designItemId,
				}),
			});
			designSnapshots.push(url);
			designRouted = designItemId;
		}
		expect(designSnapshots[0]).toBe(designHome);
		expect(designSnapshots.slice(1).every(url => url === designB)).toBe(true);
		expect(designSnapshots.some(url => url.includes("design=poster-a"))).toBe(
			false,
		);
		expect(designItemId).toBe("poster-b");
		expect(designHistoryLen).toBe(3);
	});

	it("session-based Design open clears A and settles once on B", () => {
		// onOpenDesign pushWorkHistory + clears designItemId at request time;
		// openSession suppresses keep-alive A until folder id for B is known.
		// Settle replaces the request-time entry — no second push.
		const designA =
			"/?mode=work&view=design&project=atlas&session=42&design=poster-a";
		const designSurface =
			"/?mode=work&view=design&project=atlas&session=42";
		const designB =
			"/?mode=work&view=design&project=atlas&session=42&design=poster-b";
		// Request-time push owns the new entry (preserves A on the previous one).
		let historyLen = 3; // [prior, A, pending-top]
		let historyOwned = true;
		let designItemId: string | null = null; // cleared on request
		let designRouted: string | null = null; // stripped top after clear
		const steps: Array<{
			prev: "start" | "studio";
			next: "start" | "studio";
			id: string | null;
		}> = [
			{ prev: "studio", next: "studio", id: null }, // still resolving
			{ prev: "studio", next: "studio", id: "poster-b" }, // folder id known
		];
		const snapshots: string[] = [];
		for (const step of steps) {
			const owned = historyOwned;
			let historyAlreadyOwned = false;
			if (owned) {
				if (step.next === "studio" && step.id != null) {
					historyOwned = false;
					historyAlreadyOwned = true;
				} else if (step.next !== "studio") {
					historyOwned = false;
				}
			}
			if (
				shouldPushFocusedItemHistory({
					prevStage: step.prev,
					nextStage: step.next,
					focusedStage: "studio",
					reportedId: step.id,
					routedId: designRouted,
					historyAlreadyOwned,
				})
			) {
				historyLen += 1;
			}
			designItemId = nextFocusedWorkItemId({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "studio",
				reportedId: step.id,
				currentId: designItemId,
			});
			const url = workRouteUrl("http://localhost/", {
				mode: "work",
				view: "design",
				projectSlug: "atlas",
				sessionId: 42,
				...workRouteFocusedItemIds({
					mode: "work",
					view: "design",
					graphItemId: null,
					designItemId,
				}),
			});
			snapshots.push(url);
			designRouted = designItemId;
		}
		expect(snapshots[0]).toBe(designSurface);
		expect(snapshots[1]).toBe(designB);
		expect(snapshots.some(url => url.includes("design=poster-a"))).toBe(
			false,
		);
		expect(designItemId).toBe("poster-b");
		// Request-time push only: history is [prior, A, B] — Back returns to A.
		expect(historyLen).toBe(3);
		expect(designA).toContain("design=poster-a");
		// Direct stable A→B (routed id present) still pushes; pending null does not.
		expect(
			shouldPushFocusedItemHistory({
				prevStage: "studio",
				nextStage: "studio",
				focusedStage: "studio",
				reportedId: "poster-b",
				routedId: "poster-a",
			}),
		).toBe(true);
		expect(
			shouldPushFocusedItemHistory({
				prevStage: "studio",
				nextStage: "studio",
				focusedStage: "studio",
				reportedId: "poster-b",
				routedId: null,
			}),
		).toBe(false);
		expect(
			shouldPushFocusedItemHistory({
				prevStage: "start",
				nextStage: "studio",
				focusedStage: "studio",
				reportedId: "poster-b",
				routedId: null,
			}),
		).toBe(true);
		// Request-time ownership suppresses settle push from any prevStage.
		expect(
			shouldPushFocusedItemHistory({
				prevStage: "start",
				nextStage: "studio",
				focusedStage: "studio",
				reportedId: "poster-b",
				routedId: null,
				historyAlreadyOwned: true,
			}),
		).toBe(false);
	});

	it("same-target Design session reopen keeps focused design query", () => {
		// onOpenDesign clears designItemId, but openSession already-open / keep-alive
		// immediately re-reports studio + scene id so the query never stays stripped.
		const designA =
			"/?mode=work&view=design&project=atlas&session=42&design=poster-a";
		let designItemId: string | null = null; // cleared on request
		let historyLen = 3; // request-time push already allocated
		// Immediate same-target report (no async gap that leaves id null).
		const step = {
			prev: "studio" as const,
			next: "studio" as const,
			id: "poster-a",
		};
		if (
			shouldPushFocusedItemHistory({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "studio",
				reportedId: step.id,
				routedId: null,
				historyAlreadyOwned: true,
			})
		) {
			historyLen += 1;
		}
		designItemId = nextFocusedWorkItemId({
			prevStage: step.prev,
			nextStage: step.next,
			focusedStage: "studio",
			reportedId: step.id,
			currentId: designItemId,
		});
		const url = workRouteUrl("http://localhost/", {
			mode: "work",
			view: "design",
			projectSlug: "atlas",
			sessionId: 42,
			...workRouteFocusedItemIds({
				mode: "work",
				view: "design",
				graphItemId: null,
				designItemId,
			}),
		});
		expect(designItemId).toBe("poster-a");
		expect(url).toBe(designA);
		expect(historyLen).toBe(3); // replace only — no extra entry
	});

	it("Design home same-session reopen uses request-time history only", () => {
		// After chrome Back to Design start/gallery, reopen still has request-time push
		// ownership; settle from start→studio must replace, not push a second entry.
		const priorSurface =
			"/?mode=work&view=chat&project=atlas&session=42";
		const designA =
			"/?mode=work&view=design&project=atlas&session=42&design=poster-a";
		let historyLen = 2; // [prior chat, request-time design top]
		let historyOwned = true; // onOpenDesign allocated the entry
		let designItemId: string | null = null;
		let designRouted: string | null = null;
		const steps: Array<{
			prev: "start" | "gallery" | "studio";
			next: "start" | "gallery" | "studio";
			id: string | null;
		}> = [
			{ prev: "start", next: "studio", id: "poster-a" }, // keep-alive same-session
			{ prev: "studio", next: "studio", id: "poster-a" },
		];
		const snapshots: string[] = [];
		for (const step of steps) {
			const owned = historyOwned;
			let historyAlreadyOwned = false;
			if (owned) {
				if (step.next === "studio" && step.id != null) {
					historyOwned = false;
					historyAlreadyOwned = true;
				} else if (step.next !== "studio") {
					historyOwned = false;
				}
			}
			if (
				shouldPushFocusedItemHistory({
					prevStage: step.prev,
					nextStage: step.next,
					focusedStage: "studio",
					reportedId: step.id,
					routedId: designRouted,
					historyAlreadyOwned,
				})
			) {
				historyLen += 1;
			}
			designItemId = nextFocusedWorkItemId({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "studio",
				reportedId: step.id,
				currentId: designItemId,
			});
			const url = workRouteUrl("http://localhost/", {
				mode: "work",
				view: "design",
				projectSlug: "atlas",
				sessionId: 42,
				...workRouteFocusedItemIds({
					mode: "work",
					view: "design",
					graphItemId: null,
					designItemId,
				}),
			});
			snapshots.push(url);
			designRouted = designItemId;
		}
		expect(snapshots.every(url => url === designA)).toBe(true);
		expect(designItemId).toBe("poster-a");
		expect(historyLen).toBe(2); // Back returns to prior surface
		expect(priorSurface).toContain("view=chat");
		// Child-initiated start→studio without request ownership still pushes.
		expect(
			shouldPushFocusedItemHistory({
				prevStage: "start",
				nextStage: "studio",
				focusedStage: "studio",
				reportedId: "poster-a",
				routedId: null,
				historyAlreadyOwned: false,
			}),
		).toBe(true);
	});

	it("cancelled Design session open returns to start instead of Loading", () => {
		// Sidebar open starts resolving (studio + no scene) → navigate away clears
		// openSession before resolve → keep-alive must land on start, not Loading.
		expect(
			designSessionOpenCancelStage({
				openSession: null,
				openDesignId: null,
				hasScene: false,
				stage: "studio",
			}),
		).toBe("start");
		// Valid keep-alive scene or a replacement design target must not be disturbed.
		expect(
			designSessionOpenCancelStage({
				openSession: null,
				openDesignId: null,
				hasScene: true,
				stage: "studio",
			}),
		).toBeNull();
		expect(
			designSessionOpenCancelStage({
				openSession: null,
				openDesignId: "poster-b",
				hasScene: false,
				stage: "studio",
			}),
		).toBeNull();
		expect(
			designSessionOpenCancelStage({
				openSession: { id: 5 },
				openDesignId: null,
				hasScene: false,
				stage: "studio",
			}),
		).toBeNull();
		expect(
			designSessionOpenCancelStage({
				openSession: null,
				openDesignId: null,
				hasScene: false,
				stage: "gallery",
			}),
		).toBeNull();

		// Cancel settle: studio→start with null id replaces only; no stale design query.
		let historyLen = 2; // [chat, request-time design top]
		let historyOwned = true;
		let designItemId: string | null = null;
		const step = { prev: "studio" as const, next: "start" as const, id: null };
		const owned = historyOwned;
		let historyAlreadyOwned = false;
		if (owned) {
			if (step.next === "studio" && step.id != null) {
				historyOwned = false;
				historyAlreadyOwned = true;
			} else if (step.next !== "studio") {
				historyOwned = false;
			}
		}
		if (
			shouldPushFocusedItemHistory({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "studio",
				reportedId: step.id,
				routedId: null,
				historyAlreadyOwned,
			})
		) {
			historyLen += 1;
		}
		designItemId = nextFocusedWorkItemId({
			prevStage: step.prev,
			nextStage: step.next,
			focusedStage: "studio",
			reportedId: step.id,
			currentId: designItemId,
		});
		const url = workRouteUrl("http://localhost/", {
			mode: "work",
			view: "design",
			projectSlug: "atlas",
			sessionId: 42,
			...workRouteFocusedItemIds({
				mode: "work",
				view: "design",
				graphItemId: null,
				designItemId,
			}),
		});
		expect(designItemId).toBeNull();
		expect(url).toBe("/?mode=work&view=design&project=atlas&session=42");
		expect(url.includes("design=")).toBe(false);
		expect(historyLen).toBe(2);
		expect(historyOwned).toBe(false);

		// lastDesign in storage + delayed session open + cancel before resolve must
		// consume one-time restore so reopen stays start with no design= and no
		// extra focused-item history entry.
		const lastDesignId = "poster-last";
		const abort = designSessionOpenAbortReset({ landedOnStart: true });
		expect(abort).toEqual({
			consumeLastDesignRestore: true,
			reportStage: "start",
		});
		expect(designSessionOpenAbortReset({ landedOnStart: false })).toEqual({
			consumeLastDesignRestore: false,
			reportStage: null,
		});
		let restoredConsumed = false;
		if (abort.consumeLastDesignRestore) restoredConsumed = true;
		// After cancel the open guards are empty (no session/design target/scene),
		// but restore must stay suppressed because the abort consumed it.
		const openSessionAfterCancel = null;
		const openDesignIdAfterCancel = null;
		const openedTargetAfterCancel = "";
		const sceneAfterCancel = null;
		const wouldRestore =
			!restoredConsumed
			&& !openSessionAfterCancel
			&& !openDesignIdAfterCancel
			&& !openedTargetAfterCancel
			&& !sceneAfterCancel
			&& !!lastDesignId;
		expect(wouldRestore).toBe(false);
		// Abort reports start+null → design id stays cleared; history does not gain lastDesign.
		let afterCancelId: string | null = null;
		let afterCancelHistory = historyLen;
		if (abort.reportStage === "start") {
			afterCancelId = nextFocusedWorkItemId({
				prevStage: "studio",
				nextStage: "start",
				focusedStage: "studio",
				reportedId: null,
				currentId: null,
			});
			if (
				shouldPushFocusedItemHistory({
					prevStage: "studio",
					nextStage: "start",
					focusedStage: "studio",
					reportedId: null,
					routedId: null,
					historyAlreadyOwned: false,
				})
			) {
				afterCancelHistory += 1;
			}
		}
		// A racing restore would have published lastDesign; consumed restore must not.
		expect(afterCancelId).toBeNull();
		expect(afterCancelId).not.toBe(lastDesignId);
		const afterCancelUrl = workRouteUrl("http://localhost/", {
			mode: "work",
			view: "design",
			projectSlug: "atlas",
			sessionId: 42,
			...workRouteFocusedItemIds({
				mode: "work",
				view: "design",
				graphItemId: null,
				designItemId: afterCancelId,
			}),
		});
		expect(afterCancelUrl.includes("design=")).toBe(false);
		expect(afterCancelHistory).toBe(2);

		// exitNonce leave to start/gallery with no scene must use the same abort plan
		// even when openSession cancel later sees stage already start (not studio).
		const exitNonceLeave = designLeaveEmptyAbort({
			hasScene: false,
			hasOpenSession: false,
			hasOpenDesignId: false,
			stageAfterLeave: "start",
		});
		expect(exitNonceLeave).toEqual({
			consumeLastDesignRestore: true,
			reportStage: "start",
			clearOpenedTarget: true,
		});
		expect(
			designLeaveEmptyAbort({
				hasScene: true,
				hasOpenSession: false,
				hasOpenDesignId: false,
				stageAfterLeave: "start",
			}),
		).toEqual({
			consumeLastDesignRestore: false,
			reportStage: null,
			clearOpenedTarget: false,
		});
		expect(
			designLeaveEmptyAbort({
				hasScene: false,
				hasOpenSession: false,
				hasOpenDesignId: true,
				stageAfterLeave: "start",
			}),
		).toEqual({
			consumeLastDesignRestore: false,
			reportStage: null,
			clearOpenedTarget: false,
		});

		// Delayed openDesign(A) + Back: openSeq invalidation means late completion
		// must not re-apply studio+A after leave consumed restore.
		let openSeq = 1;
		const openDesignSeqAtStart = openSeq;
		openSeq += 1; // exitNonce aborts folder-id loads too
		const lateOpenDesignCompletes =
			openDesignSeqAtStart === openSeq; // would setScene(A)+studio
		expect(lateOpenDesignCompletes).toBe(false);
		let designItemAfterOpenAbort: string | null = null;
		if (exitNonceLeave.reportStage === "start") {
			designItemAfterOpenAbort = nextFocusedWorkItemId({
				prevStage: "studio",
				nextStage: "start",
				focusedStage: "studio",
				reportedId: null,
				currentId: "poster-a",
			});
		}
		expect(designItemAfterOpenAbort).toBeNull();

		// In-flight session open + native Back/popstate: cancel request-scoped
		// pendingDesign while still applying stable route designId.
		expect(workRouteDesignOpenSync({ routeDesignId: null })).toEqual({
			pendingDesign: null,
			pendingDesignId: null,
			designOpenHistoryOwned: false,
		});
		expect(workRouteDesignOpenSync({ routeDesignId: "poster-b" })).toEqual({
			pendingDesign: null,
			pendingDesignId: "poster-b",
			designOpenHistoryOwned: false,
		});
		const afterPop = workRouteDesignOpenSync({ routeDesignId: null });
		expect(afterPop.pendingDesign).toBeNull();
		// Reopen Design after pop is start/home (empty abort), not a stuck openSession.
		const reopenAfterPop = designLeaveEmptyAbort({
			hasScene: false,
			hasOpenSession: !!afterPop.pendingDesign,
			hasOpenDesignId: !!afterPop.pendingDesignId,
			stageAfterLeave: "start",
		});
		expect(reopenAfterPop.reportStage).toBe("start");
		expect(reopenAfterPop.consumeLastDesignRestore).toBe(true);
		const reopenUrl = workRouteUrl("http://localhost/", {
			mode: "work",
			view: "design",
			projectSlug: "atlas",
			sessionId: 42,
			...workRouteFocusedItemIds({
				mode: "work",
				view: "design",
				graphItemId: null,
				designItemId: null,
			}),
		});
		expect(reopenUrl.includes("design=")).toBe(false);
		expect(afterCancelHistory).toBe(2);
	});

	it("stale already-open Design claim falls through after template replace", () => {
		// Session A opened → Back keeps openedTargetRef → template replaces scene
		// (no sessionId) → reopen A must not publish the template id.
		const targetKey = "atlas:session:5";
		expect(
			designSessionKeepAliveMatches({
				openedTargetKey: targetKey,
				targetKey,
				scene: { id: "poster-a", sessionId: 5 },
				openSessionId: 5,
			}),
		).toBe(true);
		expect(
			designSessionKeepAliveMatches({
				openedTargetKey: targetKey,
				targetKey,
				scene: { id: "template-new", sessionId: null },
				openSessionId: 5,
			}),
		).toBe(false);
		expect(
			designSessionKeepAliveMatches({
				openedTargetKey: targetKey,
				targetKey,
				scene: { id: "other", sessionId: 9 },
				openSessionId: 5,
			}),
		).toBe(false);

		// After stale reject + authenticated resolve, settle owns replace-only history.
		let historyLen = 2; // [home, request-time open]
		let historyOwned = true;
		let designItemId: string | null = null;
		let designRouted: string | null = null;
		const resolveSteps: Array<{
			prev: "start" | "studio";
			next: "start" | "studio";
			id: string | null;
		}> = [
			{ prev: "start", next: "studio", id: null }, // resolving after stale clear
			{ prev: "studio", next: "studio", id: "poster-a" },
		];
		const snapshots: string[] = [];
		for (const step of resolveSteps) {
			const owned = historyOwned;
			let historyAlreadyOwned = false;
			if (owned) {
				if (step.next === "studio" && step.id != null) {
					historyOwned = false;
					historyAlreadyOwned = true;
				} else if (step.next !== "studio") {
					historyOwned = false;
				}
			}
			if (
				shouldPushFocusedItemHistory({
					prevStage: step.prev,
					nextStage: step.next,
					focusedStage: "studio",
					reportedId: step.id,
					routedId: designRouted,
					historyAlreadyOwned,
				})
			) {
				historyLen += 1;
			}
			designItemId = nextFocusedWorkItemId({
				prevStage: step.prev,
				nextStage: step.next,
				focusedStage: "studio",
				reportedId: step.id,
				currentId: designItemId,
			});
			snapshots.push(
				workRouteUrl("http://localhost/", {
					mode: "work",
					view: "design",
					projectSlug: "atlas",
					sessionId: 42,
					...workRouteFocusedItemIds({
						mode: "work",
						view: "design",
						graphItemId: null,
						designItemId,
					}),
				}),
			);
			designRouted = designItemId;
		}
		expect(snapshots[0]).toBe(
			"/?mode=work&view=design&project=atlas&session=42",
		);
		expect(snapshots[1]).toBe(
			"/?mode=work&view=design&project=atlas&session=42&design=poster-a",
		);
		expect(snapshots.some(url => url.includes("design=template-new"))).toBe(
			false,
		);
		expect(designItemId).toBe("poster-a");
		expect(historyLen).toBe(2);
	});

	it("keeps a Work session across Delegate refresh and falls back only when catalogs prove it gone", () => {
		const masterProject = {
			slug: "master",
			name: "Master",
			visibility: "private",
			role: "owner",
		} as Project;
		const betaProject = {
			slug: "beta",
			name: "Beta",
			visibility: "private",
			role: "owner",
		} as Project;
		// In-flight submit refresh completing after Work→Delegate must not wipe session A.
		expect(
			resolvePreservedWorkSelection({
				projects: [masterProject, betaProject],
				sessions: [master, masterNewer, beta],
				activeProject: masterProject,
				activeSession: master,
			}),
		).toEqual({ project: masterProject, session: master });
		// Explicit New chat (null session) stays blank while the project remains.
		expect(
			resolvePreservedWorkSelection({
				projects: [masterProject, betaProject],
				sessions: [master, masterNewer, beta],
				activeProject: masterProject,
				activeSession: null,
			}),
		).toEqual({ project: masterProject, session: null });
		// Deleted session falls back inside the same project.
		expect(
			resolvePreservedWorkSelection({
				projects: [masterProject, betaProject],
				sessions: [masterNewer, beta],
				activeProject: masterProject,
				activeSession: master,
			}),
		).toEqual({ project: masterProject, session: masterNewer });
		// Deleted project falls back to another available project + its recent session.
		expect(
			resolvePreservedWorkSelection({
				projects: [betaProject],
				sessions: [beta],
				activeProject: masterProject,
				activeSession: master,
			}),
		).toEqual({ project: betaProject, session: beta });
	});

	it("routes Open Master conversation through Delegate and keeps a valid origin focus id", () => {
		expect(planOpenMasterConversation(21)).toEqual({
			enterDelegate: true,
			pendingMasterMessageId: 21,
		});
		expect(planOpenMasterConversation(null)).toEqual({
			enterDelegate: true,
			pendingMasterMessageId: null,
		});
		expect(planOpenMasterConversation(-1)).toEqual({
			enterDelegate: true,
			pendingMasterMessageId: null,
		});
	});

	it("restores the exact Project Ops migration route after reload", () => {
		expect(
			opsMigrationSlugFromHash("#settings/projects/legacy%20collision/ops-migration"),
		).toBe("legacy collision");
		expect(opsMigrationSlugFromHash("#settings/projects/legacy-collision")).toBeNull();
		expect(opsMigrationSlugFromHash("#settings/projects/%E0%A4%A/ops-migration")).toBeNull();
	});

	it("pins shell scope to the routed migration project after reload", () => {
		const alpha = { slug: "alpha" } as Project;
		const recovery = { slug: "recovery" } as Project;
		expect(projectForShellScope({
			projects: [alpha, recovery],
			migrationSlug: recovery.slug,
			sessionProjectSlug: alpha.slug,
			currentProject: alpha,
		})).toBe(recovery);
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
