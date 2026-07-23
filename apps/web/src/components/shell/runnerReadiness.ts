import type { Profile } from "../../types";

/** One runner's installed/auth readiness from GET /api/runners/detect. */
export type RunnerReady = {
	id: string;
	displayName: string;
	installed: boolean;
	ready: boolean;
	authHint?: string;
};

export type RunnerReadinessMap = Record<string, RunnerReady>;

/**
 * Badge for an agent-profile option in chat/task pickers.
 * Ready profiles show the runner name so "Default" is not opaque;
 * not-ready profiles say "not ready" so the banner's "pick another agent"
 * maps to a concrete choice in the list.
 */
export function profileAgentBadge(
	profile: Pick<Profile, "runner_id" | "name">,
	readiness: RunnerReadinessMap | null | undefined,
): string | undefined {
	const rid = (profile.runner_id || "").trim();
	if (!rid) return undefined;
	const entry = readiness?.[rid];
	if (!entry) return undefined;
	if (!entry.ready) return "not ready";
	// Ready profiles only badge the runner when it differs from the profile name
	// ("Default" → Hermes; a profile already named "Pi" stays unbadged).
	const label = (entry.displayName || rid).trim();
	if (!label) return undefined;
	if (label.toLowerCase() === (profile.name || "").trim().toLowerCase()) {
		return undefined;
	}
	return label;
}

/** Dropdown option for a profile, with readiness badge when known. */
export function profileAgentOption(
	profile: Pick<Profile, "id" | "name" | "runner_id">,
	readiness: RunnerReadinessMap | null | undefined,
): { value: string; label: string; badge?: string } {
	const badge = profileAgentBadge(profile, readiness);
	return {
		value: String(profile.id),
		label: profile.name,
		...(badge ? { badge } : {}),
	};
}

/** Runner-picker badge on the Agents settings cards. */
export function runnerOptionBadge(entry: RunnerReady): string | undefined {
	if (!entry.installed) return undefined;
	return entry.ready ? "ready" : "not ready";
}

/** Card chip + tone for Settings → Agents runner grid. */
export type RunnerGridStatus = {
	label: string;
	tone: "ready" | "detected" | "missing" | "not-ready";
	hint?: string;
};

/**
 * Status for one runner card. Adapter presence alone is not enough: a
 * Runnable binary with revoked auth must read "Not ready" so the grid
 * matches the top Hermes banner and profile pickers.
 */
export function runnerGridStatus(
	runner: { id: string; installed: boolean; runnable: boolean },
	readiness: RunnerReadinessMap | null | undefined,
): RunnerGridStatus {
	if (!runner.installed) return { label: "Missing", tone: "missing" };
	if (!runner.runnable) return { label: "Future adapter", tone: "detected" };
	const entry = readiness?.[runner.id];
	if (entry && !entry.ready) {
		return {
			label: "Not ready",
			tone: "not-ready",
			...(entry.authHint ? { hint: entry.authHint } : {}),
		};
	}
	if (entry?.ready) return { label: "Ready", tone: "ready" };
	// Readiness still loading or unknown — keep the established adapter word.
	return { label: "Runnable", tone: "ready" };
}
