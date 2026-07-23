import { describe, expect, it } from "vitest";
import { collabCardAriaLabel, resultCardAriaLabel, stripRunnerPreamble } from "./ChatThread";

describe("resultCardAriaLabel", () => {
	it("spaces type and title so Open is not smashed into the path", () => {
		expect(
			resultCardAriaLabel({
				type: "image",
				title: "chat-1.png",
				path: "artifacts/media/images/chat-1.png",
			}),
		).toBe("Open Image, chat-1.png");
		expect(
			resultCardAriaLabel({
				type: "doc",
				title: "",
				path: "reports/note.md",
			}),
		).toBe("Open Document, reports/note.md");
	});
});

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

	it("drops the dump when the answer starts with bold, not a ## heading", () => {
		const raw =
			"pi v0.80.10 --- ## Skills - /home/user/.pi/agent/skills/tdd/SKILL.md - /home/user/.agents/skills/qa/SKILL.md --- **Core idea:** Name two flavors that sound like a mismatched couple.";
		expect(stripRunnerPreamble(raw)).toBe(
			"**Core idea:** Name two flavors that sound like a mismatched couple.",
		);
	});

	it("leaves ordinary answers untouched", () => {
		const body = "## Position\n\nPlain text wins for tiny lists.";
		expect(stripRunnerPreamble(body)).toBe(body);
	});
});
