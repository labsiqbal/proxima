import { describe, expect, it } from "vitest";
import { collabCardAriaLabel, stripRunnerPreamble } from "./ChatThread";

describe("collabCardAriaLabel", () => {
	it("keeps agent, lane, status, and action spaced without body text", () => {
		expect(
			collabCardAriaLabel(
				{
					agentName: "Default",
					roundLabel: "Idea lane 1",
					status: "done",
				},
				true,
			),
		).toBe("Default, Idea lane 1, done. Expand");

		expect(
			collabCardAriaLabel(
				{
					agentName: "Pi",
					roundLabel: "Idea lane 2",
					status: "running",
				},
				false,
			),
		).toBe("Pi, Idea lane 2, running. Collapse");
	});
});

describe("stripRunnerPreamble", () => {
	it("drops Pi version + skills catalog before the real heading", () => {
		const raw =
			"pi v0.80.10 --- ## Skills - /home/user/.pi/agent/skills/tdd/SKILL.md - /home/user/.agents/skills/qa/SKILL.md --- New version available: v0.81.1 (installed v0.80.10). Run: `npm i -g @earendil-works/pi-coding-agent` ## Position **Use SQLite.** ## Rebuttal Files reinvent a worse DB.";
		expect(stripRunnerPreamble(raw)).toBe(
			"## Position **Use SQLite.** ## Rebuttal Files reinvent a worse DB.",
		);
	});

	it("leaves ordinary answers untouched", () => {
		const body = "## Position\n\nPlain text wins for tiny lists.";
		expect(stripRunnerPreamble(body)).toBe(body);
	});
});
