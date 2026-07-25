import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatThread } from "./ChatThread";
import type { ChatMessage } from "../../types";
import { applyThreadScrollFollow } from "./threadScroll";

const stylesSource = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), "../../styles.css"),
	"utf8",
);

vi.mock("../../api/sessions", () => ({
	previewTurnRestore: vi.fn(),
	restoreTurn: vi.fn(),
}));
vi.mock("../../api/runs", () => ({
	respondPermission: vi.fn(),
}));
vi.mock("../../api/files", () => ({
	designFromImage: vi.fn(),
	previewUrl: vi.fn(() => ""),
}));
vi.mock("../ui/Dialog", () => ({
	confirmDialog: vi.fn(async () => true),
}));

const fewMessages: ChatMessage[] = [
	{
		id: 1,
		role: "user",
		content: "hello",
		created_at: "2026-07-25T12:00:00Z",
	},
	{
		id: 2,
		role: "assistant",
		content: "Hi there",
		created_at: "2026-07-25T12:00:05Z",
	},
];

describe("ChatThread top-anchor layout", () => {
	it("places chat-log as the first child of .thread (not flex-end spacer)", () => {
		const { container } = render(
			<ChatThread messages={fewMessages} events={[]} />,
		);
		const thread = container.querySelector(".thread");
		expect(thread).toBeTruthy();
		const first = thread!.firstElementChild;
		expect(first).toHaveClass("chat-log");
		// Messages render; empty state does not.
		expect(screen.queryByTestId("chat-empty")).not.toBeInTheDocument();
		expect(screen.getByText("hello")).toBeInTheDocument();
		expect(screen.getByText("Hi there")).toBeInTheDocument();
	});

	it("keeps ChatEmpty as the only log content when idle/empty", () => {
		const { container } = render(<ChatThread messages={[]} events={[]} />);
		const log = container.querySelector(".chat-log");
		expect(log).toBeTruthy();
		expect(screen.getByTestId("chat-empty")).toBeInTheDocument();
		// Empty remains the designed sparse teaching surface inside the log.
		expect(log!.querySelector(".chat-empty")).toBeTruthy();
	});

	it("CSS packs .thread from the top and does not pin .chat-log to the end", () => {
		// Guard the contract in the stylesheet (jsdom does not apply the full
		// app stylesheet to getComputedStyle for these rules reliably).
		expect(stylesSource).toMatch(
			/\.thread\s*\{[^}]*justify-content:\s*flex-start/s,
		);
		const chatLogBodies = [
			...stylesSource.matchAll(/\.chat-log\s*\{([^}]*)\}/g),
		].map((m) => m[1].replace(/\/\*[\s\S]*?\*\//g, ""));
		expect(chatLogBodies.length).toBeGreaterThan(0);
		for (const body of chatLogBodies) {
			expect(body).not.toMatch(/margin-top:\s*auto/);
			expect(body).not.toMatch(/min-height:\s*100%/);
		}
		expect(stylesSource).toMatch(/\.chat-log\s*\{[^}]*flex:\s*0\s+0\s+auto/s);
	});
});

describe("ChatThread scroll follow (layout metrics)", () => {
	it("short content: applyThreadScrollFollow does not force bottom", () => {
		const el = { scrollHeight: 180, clientHeight: 640, scrollTop: 0 };
		applyThreadScrollFollow(el, true);
		expect(el.scrollTop).toBe(0);
	});

	it("overflowing + pinned: still follows to latest", () => {
		const el = { scrollHeight: 2400, clientHeight: 640, scrollTop: 100 };
		applyThreadScrollFollow(el, true);
		expect(el.scrollTop).toBe(2400);
	});
});
