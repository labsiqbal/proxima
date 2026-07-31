import { describe, expect, it } from "vitest";
import {
	AGENT_PICKER_LOCKED_REASON,
	chatDraftScopeKey,
	isAgentPickerLocked,
	isAgentTurnSlashCommand,
} from "./ChatScreen";

describe("isAgentTurnSlashCommand", () => {
	it("routes masterplan with or without an idea to the agent", () => {
		expect(isAgentTurnSlashCommand("/masterplan")).toBe(true);
		expect(isAgentTurnSlashCommand("/masterplan build a CLI")).toBe(true);
		expect(isAgentTurnSlashCommand("/masterplanner")).toBe(false);
		expect(isAgentTurnSlashCommand("/masterplan-foo")).toBe(false);
		expect(isAgentTurnSlashCommand("/status")).toBe(false);
	});

	it("routes enabled skill slash names from the catalog", () => {
		expect(isAgentTurnSlashCommand("/grill-with-docs", ["/grill-with-docs"])).toBe(true);
		expect(isAgentTurnSlashCommand("/grill-with-docs freeform", ["/grill-with-docs"])).toBe(true);
		expect(isAgentTurnSlashCommand("/grill-with-docs", [])).toBe(false);
		expect(isAgentTurnSlashCommand("/help", ["/grill-with-docs"])).toBe(false);
	});
});

describe("isAgentPickerLocked", () => {
	it("locks while a run is queued or running for the session", () => {
		expect(isAgentPickerLocked(42)).toBe(true);
		expect(isAgentPickerLocked(1)).toBe(true);
	});

	it("unlocks when the session is clean (no busy run)", () => {
		expect(isAgentPickerLocked(null)).toBe(false);
		expect(isAgentPickerLocked(undefined)).toBe(false);
	});

	it("exposes a stable lock reason for title and aria", () => {
		expect(AGENT_PICKER_LOCKED_REASON).toBe(
			"Agent locked while a run is in progress",
		);
	});
});

describe("chatDraftScopeKey", () => {
	it("keeps drafts isolated by producing session across projects", () => {
		expect(chatDraftScopeKey({ id: 7 }, { slug: "master" })).toBe("session:7");
		expect(chatDraftScopeKey({ id: 9 }, { slug: "client" })).toBe("session:9");
	});

	it("keeps each project's new-chat draft separate", () => {
		expect(chatDraftScopeKey(null, { slug: "master" })).toBe("new:master");
		expect(chatDraftScopeKey(null, { slug: "client" })).toBe("new:client");
		expect(chatDraftScopeKey(null, null)).toBe("new:unscoped");
	});
});
