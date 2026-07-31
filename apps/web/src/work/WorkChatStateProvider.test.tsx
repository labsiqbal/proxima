import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
	parseWorkChatState,
	useWorkChatState,
	WorkChatStateProvider,
} from "./WorkChatStateProvider";

function StateHarness({
	project,
	session,
}: {
	project: string;
	session: number;
}) {
	const chat = useWorkChatState(project, session);
	return (
		<>
			<output data-testid="draft">{chat.state.draft}</output>
			<button
				onClick={() =>
					chat.update({
						draft: `${project} draft`,
						selection: { start: 1, end: 4 },
						mode: "brainstorm",
						attachments: [
							{ path: "uploads/reference.txt", name: "reference.txt", img: false },
						],
						scrollTop: 123,
					})
				}
			>
				Save
			</button>
		</>
	);
}

describe("WorkChatStateProvider", () => {
	beforeEach(() => localStorage.clear());

	it("persists complete session state independently per project", () => {
		const { rerender } = render(
			<WorkChatStateProvider
				ownerId={7}
				availableKeys={["atlas:new", "atlas:11", "borealis:new", "borealis:22"]}
			>
				<StateHarness project="atlas" session={11} />
			</WorkChatStateProvider>,
		);
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		expect(screen.getByTestId("draft")).toHaveTextContent("atlas draft");

		rerender(
			<WorkChatStateProvider
				ownerId={7}
				availableKeys={["atlas:new", "atlas:11", "borealis:new", "borealis:22"]}
			>
				<StateHarness project="borealis" session={22} />
			</WorkChatStateProvider>,
		);
		expect(screen.getByTestId("draft")).toHaveTextContent("");
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		const stored = parseWorkChatState(
			localStorage.getItem("proxima.work-chat-state.v1.7"),
		);
		expect(stored["atlas:11"]).toMatchObject({
			draft: "atlas draft",
			selection: { start: 1, end: 4 },
			mode: "brainstorm",
			scrollTop: 123,
		});
		expect(stored["borealis:22"].draft).toBe("borealis draft");
	});

	it("drops deleted projects and rejects unsafe persisted attachments", async () => {
		localStorage.setItem(
			"proxima.work-chat-state.v1.7",
			JSON.stringify({
				"atlas:11": {
					draft: "safe",
					selection: { start: 0, end: 4 },
					mode: "chat",
					attachments: [
						{ path: "uploads/safe.txt", name: "safe.txt", img: false },
						{ path: "../secret.txt", name: "secret.txt", img: false },
						{ path: "/etc/passwd", name: "passwd", img: false },
						{ path: "C:\\secret.txt", name: "secret.txt", img: false },
					],
					scrollTop: 50,
				},
				"deleted:99": {
					draft: "must not leak",
					selection: { start: 0, end: 0 },
					mode: "chat",
					attachments: [],
					scrollTop: 0,
				},
			}),
		);

		render(
			<WorkChatStateProvider
				ownerId={7}
				availableKeys={["atlas:new", "atlas:11"]}
				availabilityReady
			>
				<StateHarness project="atlas" session={11} />
			</WorkChatStateProvider>,
		);
		expect(screen.getByTestId("draft")).toHaveTextContent("safe");

		await screen.findByText("safe");
		const stored = parseWorkChatState(
			localStorage.getItem("proxima.work-chat-state.v1.7"),
		);
		expect(stored["deleted:99"]).toBeUndefined();
		expect(stored["atlas:11"].attachments).toEqual([
			{ path: "uploads/safe.txt", name: "safe.txt", img: false },
		]);
	});

	it("does not wipe persisted state while the project catalog is still loading", async () => {
		localStorage.setItem(
			"proxima.work-chat-state.v1.7",
			JSON.stringify({
				"atlas:11": {
					draft: "keep me",
					selection: { start: 0, end: 7 },
					mode: "chat",
					attachments: [],
					scrollTop: 40,
				},
			}),
		);

		const { rerender } = render(
			<WorkChatStateProvider
				ownerId={7}
				availableKeys={[]}
				availabilityReady={false}
			>
				<StateHarness project="atlas" session={11} />
			</WorkChatStateProvider>,
		);
		expect(screen.getByTestId("draft")).toHaveTextContent("keep me");
		expect(
			parseWorkChatState(localStorage.getItem("proxima.work-chat-state.v1.7"))[
				"atlas:11"
			].draft,
		).toBe("keep me");

		rerender(
			<WorkChatStateProvider
				ownerId={7}
				availableKeys={["atlas:new", "atlas:11"]}
				availabilityReady
			>
				<StateHarness project="atlas" session={11} />
			</WorkChatStateProvider>,
		);
		expect(screen.getByTestId("draft")).toHaveTextContent("keep me");
	});

	it("prunes everything once a confirmed-empty catalog is ready", async () => {
		localStorage.setItem(
			"proxima.work-chat-state.v1.7",
			JSON.stringify({
				"ghost:1": {
					draft: "gone",
					selection: { start: 0, end: 0 },
					mode: "chat",
					attachments: [],
					scrollTop: 0,
				},
			}),
		);

		const { rerender } = render(
			<WorkChatStateProvider
				ownerId={7}
				availableKeys={[]}
				availabilityReady={false}
			>
				<StateHarness project="ghost" session={1} />
			</WorkChatStateProvider>,
		);
		expect(screen.getByTestId("draft")).toHaveTextContent("gone");

		rerender(
			<WorkChatStateProvider
				ownerId={7}
				availableKeys={[]}
				availabilityReady
			>
				<StateHarness project="ghost" session={1} />
			</WorkChatStateProvider>,
		);
		await screen.findByTestId("draft");
		expect(screen.getByTestId("draft")).toHaveTextContent("");
		expect(
			parseWorkChatState(localStorage.getItem("proxima.work-chat-state.v1.7")),
		).toEqual({});
	});
});
