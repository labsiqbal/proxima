import type { ShellMode } from "../components/shell/ShellModeSwitch";
import type { View } from "../types";

const WORK_VIEWS = new Set<View>([
	"chat",
	"activity",
	"workflows",
	"artifacts",
	"design",
	"inbox",
]);
const DELEGATE_VIEWS = new Set<View>([
	"master",
	"activity",
	"artifacts",
	"inbox",
]);
// Artifacts replaced the Files destination (ADR-0043); a bookmark or a restored
// tab still carrying the old name lands on its successor instead of the default.
const RENAMED_VIEWS: Record<string, View> = { files: "artifacts" };

export type WorkRoute = {
	mode: ShellMode;
	view: View;
	projectSlug: string | null;
	sessionId: number | null;
	workflowJobId: number | null;
	designId: string | null;
};

function positiveInteger(value: string | null): number | null {
	if (!value || !/^\d+$/.test(value)) return null;
	const parsed = Number(value);
	return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

export function parseWorkRoute(location: {
	search: string;
	hash: string;
}): WorkRoute {
	const params = new URLSearchParams(location.search);
	const mode: ShellMode =
		params.get("mode") === "delegate" ? "delegate" : "work";
	const named = params.get("view");
	const requestedView = (named && RENAMED_VIEWS[named]
		? RENAMED_VIEWS[named]
		: named) as View | null;
	const allowed = mode === "delegate" ? DELEGATE_VIEWS : WORK_VIEWS;
	let view: View =
		requestedView && allowed.has(requestedView)
			? requestedView
			: mode === "delegate"
				? "master"
				: "chat";
	if (/^#task\/\d+$/.test(location.hash)) view = "task";
	// Record permalinks outlive both the retired Archive destination (#139) and
	// the Files one (#144): they open as the record panel inside Artifacts.
	if (/^#archive\/[^/]+\/[^/]+$/.test(location.hash)) view = "artifacts";
	return {
		mode,
		view,
		projectSlug: mode === "work" ? params.get("project") : null,
		sessionId:
			mode === "work"
				? positiveInteger(params.get("session"))
				: null,
		workflowJobId:
			mode === "work" && view === "workflows"
				? positiveInteger(params.get("workflow"))
				: null,
		designId:
			mode === "work" && view === "design"
				? params.get("design") || null
				: null,
	};
}

export function workRouteUrl(
	current: string,
	route: WorkRoute,
): string {
	const url = new URL(current);
	for (const name of [
		"mode",
		"view",
		"project",
		"session",
		"workflow",
		"design",
	]) {
		url.searchParams.delete(name);
	}
	url.searchParams.set("mode", route.mode);
	url.searchParams.set("view", route.view);
	if (route.mode === "work" && route.projectSlug) {
		url.searchParams.set("project", route.projectSlug);
	}
	if (
		route.mode === "work" &&
		route.sessionId != null
	) {
		url.searchParams.set("session", String(route.sessionId));
	}
	if (
		route.mode === "work" &&
		route.view === "workflows" &&
		route.workflowJobId != null
	) {
		url.searchParams.set("workflow", String(route.workflowJobId));
	}
	if (
		route.mode === "work" &&
		route.view === "design" &&
		route.designId
	) {
		url.searchParams.set("design", route.designId);
	}
	if (route.view !== "task" && route.view !== "artifacts") url.hash = "";
	return `${url.pathname}${url.search}${url.hash}`;
}
