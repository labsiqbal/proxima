import { describe, expect, it } from "vitest";
import {
	profileAgentBadge,
	profileAgentOption,
	runnerOptionBadge,
	type RunnerReadinessMap,
} from "./runnerReadiness";

const readiness: RunnerReadinessMap = {
	hermes: {
		id: "hermes",
		displayName: "Hermes",
		installed: true,
		ready: false,
		authHint: "re-auth",
	},
	pi: {
		id: "pi",
		displayName: "Pi",
		installed: true,
		ready: true,
	},
	claude: {
		id: "claude-code",
		displayName: "Claude Code",
		installed: false,
		ready: false,
	},
};

describe("profileAgentBadge", () => {
	it("marks not-ready runners so Default is selectable with eyes open", () => {
		expect(
			profileAgentBadge({ name: "Default", runner_id: "hermes" }, readiness),
		).toBe("not ready");
	});

	it("shows the runner name when the profile is ready and named differently", () => {
		expect(
			profileAgentBadge({ name: "Research", runner_id: "pi" }, readiness),
		).toBe("Pi");
	});

	it("omits a ready runner badge when it duplicates the profile name", () => {
		expect(
			profileAgentBadge({ name: "Pi", runner_id: "pi" }, readiness),
		).toBeUndefined();
	});

	it("returns undefined until readiness loads or runner is unknown", () => {
		expect(
			profileAgentBadge({ name: "Default", runner_id: "hermes" }, null),
		).toBeUndefined();
		expect(
			profileAgentBadge({ name: "X", runner_id: "missing" }, readiness),
		).toBeUndefined();
		expect(
			profileAgentBadge({ name: "X", runner_id: "" }, readiness),
		).toBeUndefined();
	});
});

describe("profileAgentOption", () => {
	it("builds a dropdown option with the readiness badge", () => {
		expect(
			profileAgentOption(
				{ id: 1, name: "Default", runner_id: "hermes" },
				readiness,
			),
		).toEqual({
			value: "1",
			label: "Default",
			badge: "not ready",
		});
		expect(
			profileAgentOption({ id: 2, name: "Pi", runner_id: "pi" }, readiness),
		).toEqual({
			value: "2",
			label: "Pi",
		});
		expect(
			profileAgentOption(
				{ id: 3, name: "Research", runner_id: "pi" },
				readiness,
			),
		).toEqual({
			value: "3",
			label: "Research",
			badge: "Pi",
		});
	});
});

describe("runnerOptionBadge", () => {
	it("labels installed runners ready or not ready", () => {
		expect(runnerOptionBadge(readiness.hermes)).toBe("not ready");
		expect(runnerOptionBadge(readiness.pi)).toBe("ready");
		expect(runnerOptionBadge(readiness.claude)).toBeUndefined();
	});
});
