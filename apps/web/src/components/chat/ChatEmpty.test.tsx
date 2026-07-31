import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ChatEmpty } from "./ChatEmpty";
import { ChatThread } from "./ChatThread";

describe("ChatEmpty", () => {
	it("renders a compact default: title + lead, no capability wall or numbered tutorial", () => {
		render(<ChatEmpty />);

		expect(
			screen.getByRole("heading", { name: "Start a conversation" }),
		).toBeInTheDocument();
		expect(screen.getByText(/Hands-on work with one agent/)).toBeInTheDocument();

		// Full teaching dump stays off the page by default.
		expect(screen.queryByLabelText("What you can do here")).not.toBeInTheDocument();
		expect(screen.queryByLabelText("Getting started")).not.toBeInTheDocument();
		expect(
			screen.queryByText(/Write a message and press/),
		).not.toBeInTheDocument();
		expect(
			screen.queryByText(/Send prompts and watch tools run live/),
		).not.toBeInTheDocument();

		// Short progressive-disclosure hints (tooltips via title / aria-label).
		expect(screen.getByLabelText(/slash commands/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/Attach files/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/@-mention/i)).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "How it works" }),
		).toBeInTheDocument();
	});

	it("opens a small help dialog with fuller copy and closes via Got it, Esc, and scrim", async () => {
		const user = userEvent.setup();
		render(<ChatEmpty />);

		const trigger = screen.getByRole("button", { name: "How it works" });
		await user.click(trigger);

		const dialog = screen.getByRole("dialog", { name: "How Chat works" });
		expect(dialog).toBeInTheDocument();
		expect(
			within(dialog).getByLabelText("What you can do here"),
		).toBeInTheDocument();
		expect(within(dialog).getByLabelText("Getting started")).toBeInTheDocument();
		expect(
			within(dialog).getByText(/Send prompts and watch tools run live/),
		).toBeInTheDocument();
		expect(
			within(dialog).getByText(/Write a message and press/),
		).toBeInTheDocument();

		await user.click(within(dialog).getByRole("button", { name: "Got it" }));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		// Focus returns to the trigger after dismiss.
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		expect(screen.getByRole("dialog")).toBeInTheDocument();
		await user.keyboard("{Escape}");
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		// Click the scrim itself (not the centered card child).
		fireEvent.click(screen.getByTestId("chat-empty-help-scrim"));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});

	it("closes via the dialog close control", async () => {
		const user = userEvent.setup();
		render(<ChatEmpty />);
		await user.click(screen.getByRole("button", { name: "How it works" }));
		await user.click(screen.getByRole("button", { name: "Close" }));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});
});

describe("ChatThread empty branch", () => {
	it("uses ChatEmpty when there are no messages and no live run", () => {
		render(
			<ChatThread messages={[]} events={[]} />,
		);
		expect(screen.getByTestId("chat-empty")).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: "Start a conversation" }),
		).toBeInTheDocument();
		expect(screen.queryByLabelText("What you can do here")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "How it works" }),
		).toBeInTheDocument();
	});

	it("does not show ChatEmpty when messages exist", () => {
		render(
			<ChatThread
				messages={[
					{
						id: 1,
						role: "user",
						content: "hello",
						created_at: "2026-07-25T12:00:00Z",
					},
				]}
				events={[]}
			/>,
		);
		expect(screen.queryByTestId("chat-empty")).not.toBeInTheDocument();
	});

	it("lets an embedded authoring surface replace the generic Chat guidance", () => {
		render(
			<ChatThread
				messages={[]}
				events={[]}
				emptyContent={<div data-testid="workflow-empty">Workflow guidance</div>}
			/>,
		);
		expect(screen.getByTestId("workflow-empty")).toHaveTextContent(
			"Workflow guidance",
		);
		expect(screen.queryByTestId("chat-empty")).not.toBeInTheDocument();
	});
});
