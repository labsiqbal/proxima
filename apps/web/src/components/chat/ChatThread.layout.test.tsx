import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatThread } from "./ChatThread";
import type { ChatMessage } from "../../types";
import { applyThreadScrollFollow } from "./threadScroll";
import { previewUrl } from "../../api/files";

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
	isSvgPath: (path: string) => /\.svg$/i.test(path),
	previewUrl: vi.fn(() => "/stable-preview"),
}));
vi.mock("../../hooks/useRawBlobUrl", () => ({
	useRawBlobUrl: vi.fn(() => ({ url: null, status: "idle", retry: () => undefined })),
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

	it("keeps optimistic message keys separate from persisted database ids", () => {
		const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
		render(
			<ChatThread
				messages={[
					...fewMessages,
					{ role: "user", content: "optimistic third message" },
				]}
				events={[]}
			/>,
		);

		expect(consoleError).not.toHaveBeenCalledWith(
			expect.stringContaining("Encountered two children with the same key"),
			expect.anything(),
		);
		consoleError.mockRestore();
	});

	it("uses a result image's canonical target for inline media", () => {
		const target = {
			project: "demo",
			area: { kind: "ops" as const, id: 12 },
			path: "artifacts/image.png",
		};
		const { container } = render(
			<ChatThread
				messages={[
					{
						id: 3,
						role: "assistant",
						content: "Created an image",
						output_links: [{
							type: "image",
							title: "image.png",
							path: "artifacts/image.png",
							target,
						}],
					},
				]}
				events={[]}
				token="token"
				slug="demo"
				onOpenOutput={() => undefined}
			/>,
		);

		expect(previewUrl).toHaveBeenCalledWith(
			"demo",
			"artifacts/image.png",
			target,
		);
		expect(container.querySelector(".result-media img")).toHaveAttribute(
			"src",
			"/stable-preview",
		);
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

	it("CSS makes .thread a real scrollport in the flex chain", () => {
		// Keep-alive adds .surface-pane between main-pane and chat-stage. Every
		// link in that flex chain must be bounded or the populated thread's
		// intrinsic height pushes the composer below the viewport.
		const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "");
		/** Match a rule whose selector is exactly `sel` (not a longer compound). */
		const exactRule = (sel: string) => {
			const esc = sel.replace(".", "\\.");
			const m = stylesSource.match(
				new RegExp(`(?:^|\\n)${esc}\\s*\\{([^}]*)\\}`),
			);
			return m ? strip(m[1]) : "";
		};
		const mainPane = exactRule(".main-pane");
		const surfacePane = exactRule(".surface-pane");
		const chatStage = exactRule(".chat-stage");
		const thread = exactRule(".thread");

		expect(mainPane).toMatch(/min-height:\s*0/);
		expect(mainPane).toMatch(/overflow:\s*hidden/);
		expect(surfacePane).toMatch(/display:\s*flex/);
		expect(surfacePane).toMatch(/flex-direction:\s*column/);
		expect(surfacePane).toMatch(/flex:\s*1/);
		expect(surfacePane).toMatch(/min-height:\s*0/);
		expect(surfacePane).toMatch(/overflow:\s*hidden/);
		expect(chatStage).toMatch(/min-height:\s*0/);
		expect(chatStage).toMatch(/overflow:\s*hidden/);
		expect(thread).toMatch(/min-height:\s*0/);
		expect(thread).toMatch(/overflow-y:\s*auto/);
		// Content child must not be the scrollport (grows with messages).
		const chatLog = exactRule(".chat-log");
		expect(chatLog).not.toMatch(/overflow(-y)?:\s*auto/);
		expect(chatLog).toMatch(/flex:\s*0\s+0\s+auto/);
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

	it("restores a persisted anchor after messages load", () => {
		const { container, rerender } = render(
			<ChatThread
				messages={[]}
				events={[]}
				scrollAnchor={123}
				scrollRestoreKey="atlas:1"
			/>,
		);
		const thread = container.querySelector(".thread") as HTMLDivElement;
		Object.defineProperty(thread, "scrollHeight", { value: 1000, configurable: true });
		Object.defineProperty(thread, "clientHeight", { value: 400, configurable: true });

		rerender(
			<ChatThread
				messages={fewMessages}
				events={[]}
				scrollAnchor={123}
				scrollRestoreKey="atlas:1"
			/>,
		);
		expect(thread.scrollTop).toBe(123);
	});

	it("restores again when switching A → B → A on the same mount", () => {
		const { container, rerender } = render(
			<ChatThread
				messages={[]}
				events={[]}
				scrollAnchor={120}
				scrollRestoreKey="atlas:1"
			/>,
		);
		const thread = container.querySelector(".thread") as HTMLDivElement;
		Object.defineProperty(thread, "scrollHeight", { value: 1000, configurable: true });
		Object.defineProperty(thread, "clientHeight", { value: 400, configurable: true });

		rerender(
			<ChatThread
				messages={fewMessages}
				events={[]}
				scrollAnchor={120}
				scrollRestoreKey="atlas:1"
			/>,
		);
		expect(thread.scrollTop).toBe(120);

		thread.scrollTop = 50;
		rerender(
			<ChatThread
				messages={fewMessages}
				events={[]}
				scrollAnchor={340}
				scrollRestoreKey="atlas:2"
			/>,
		);
		expect(thread.scrollTop).toBe(340);

		thread.scrollTop = 10;
		rerender(
			<ChatThread
				messages={fewMessages}
				events={[]}
				scrollAnchor={120}
				scrollRestoreKey="atlas:1"
			/>,
		);
		expect(thread.scrollTop).toBe(120);
	});

	it("does not persist the transient zero position of a hidden surface", () => {
		const onScrollAnchorChange = vi.fn();
		const { container } = render(
			<div hidden>
				<ChatThread
					messages={fewMessages}
					events={[]}
					scrollAnchor={123}
					onScrollAnchorChange={onScrollAnchorChange}
				/>
			</div>,
		);
		const thread = container.querySelector(".thread") as HTMLDivElement;
		thread.scrollTop = 0;
		fireEvent.scroll(thread);
		expect(onScrollAnchorChange).not.toHaveBeenCalled();
	});

	it("defers anchor restore until a keep-alive surface becomes visible", () => {
		const { container, rerender } = render(
			<div hidden>
				<ChatThread
					messages={fewMessages}
					events={[]}
					scrollAnchor={123}
					scrollRestoreKey="atlas:1"
					surfaceActive={false}
				/>
			</div>,
		);
		const thread = container.querySelector(".thread") as HTMLDivElement;
		Object.defineProperty(thread, "scrollHeight", {
			value: 1000,
			configurable: true,
		});
		Object.defineProperty(thread, "clientHeight", {
			value: 0,
			configurable: true,
		});
		expect(thread.scrollTop).toBe(0);

		Object.defineProperty(thread, "clientHeight", {
			value: 400,
			configurable: true,
		});
		rerender(
			<div>
				<ChatThread
					messages={fewMessages}
					events={[]}
					scrollAnchor={123}
					scrollRestoreKey="atlas:1"
					surfaceActive
				/>
			</div>,
		);
		expect(thread.scrollTop).toBe(123);
	});
});
