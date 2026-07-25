import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AlphaEmpty } from "./AlphaScreen";

describe("Alpha empty surface", () => {
	it("renders compact default without capability wall or numbered tutorial", () => {
		render(<AlphaEmpty onExample={vi.fn()} />);

		expect(screen.getByRole("heading", { name: "Delegate an outcome" })).toBeInTheDocument();
		expect(screen.getByText(/Dispatch durable jobs/)).toBeInTheDocument();
		expect(screen.queryByLabelText("What you can do here")).not.toBeInTheDocument();
		expect(screen.queryByLabelText("Getting started")).not.toBeInTheDocument();
		expect(
			screen.queryByText(/Describe an outcome and press Delegate/),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "How it works" })).toBeInTheDocument();
		expect(screen.getByLabelText("Example delegations")).toBeInTheDocument();
		expect(screen.getByTestId("alpha-empty")).toBeInTheDocument();
	});

	it("opens How it works and dismisses via Got it, Esc, and scrim", async () => {
		const user = userEvent.setup();
		render(<AlphaEmpty onExample={vi.fn()} />);

		const trigger = screen.getByRole("button", { name: "How it works" });
		await user.click(trigger);

		const dialog = screen.getByRole("dialog", { name: "How Alpha works" });
		expect(within(dialog).getByLabelText("What you can do here")).toBeInTheDocument();
		expect(within(dialog).getByLabelText("Getting started")).toBeInTheDocument();
		expect(
			within(dialog).getByText(/Describe an outcome and press Delegate/),
		).toBeInTheDocument();

		await user.click(within(dialog).getByRole("button", { name: "Got it" }));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		await user.keyboard("{Escape}");
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		fireEvent.click(screen.getByTestId("alpha-empty-help-scrim"));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});
});
