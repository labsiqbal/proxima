import { describe, expect, it } from "vitest";
import { collabCardAriaLabel } from "./ChatThread";

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
