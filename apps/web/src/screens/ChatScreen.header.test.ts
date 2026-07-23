import { describe, expect, it } from "vitest";
import { chatHeaderProjectLabel, isAgentTurnSlashCommand } from "./ChatScreen";
import type { ChatSession, Project } from "../types";

const projects: Project[] = [
	{
		slug: "gnhf-e2e-projects",
		name: "gnhf-e2e-projects",
		path: "/tmp/gnhf",
		owner: "owner",
		role: "owner",
		visibility: "private",
	},
	{
		slug: "linc",
		name: "Linc",
		path: "/tmp/linc",
		owner: "owner",
		role: "owner",
		visibility: "private",
	},
];

const shellProject = projects[0];

const lincSession = {
	id: 17,
	title: "E2E Chat Ok",
	project_slug: "linc",
	project_name: "Linc",
	profile_name: "Default",
} as ChatSession;

describe("isAgentTurnSlashCommand", () => {
	it("routes masterplan with or without an idea to the agent", () => {
		expect(isAgentTurnSlashCommand("/masterplan")).toBe(true);
		expect(isAgentTurnSlashCommand("/masterplan build a CLI")).toBe(true);
		expect(isAgentTurnSlashCommand("/masterplanner")).toBe(false);
		expect(isAgentTurnSlashCommand("/status")).toBe(false);
	});
});

describe("chatHeaderProjectLabel", () => {
	it("prefers the open session project over a desynced shell pick", () => {
		expect(chatHeaderProjectLabel(lincSession, shellProject, projects)).toBe("Linc");
	});

	it("falls back to session.project_name when the project list has no match", () => {
		expect(chatHeaderProjectLabel(lincSession, shellProject, [])).toBe("Linc");
	});

	it("uses the shell project for a blank new chat", () => {
		expect(chatHeaderProjectLabel(null, shellProject, projects)).toBe("gnhf-e2e-projects");
	});

	it("says No project when neither session nor shell has one", () => {
		expect(chatHeaderProjectLabel(null, null, projects)).toBe("No project");
	});
});
