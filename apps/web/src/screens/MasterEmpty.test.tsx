import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MasterEmpty } from "./MasterScreen";

describe("Master empty surface", () => {
	it("renders compact default without capability wall or numbered tutorial", () => {
		render(<MasterEmpty onExample={vi.fn()} />);

		expect(screen.getByRole("heading", { name: "Delegate an outcome" })).toBeInTheDocument();
		expect(screen.getByText(/Dispatch durable jobs/)).toBeInTheDocument();
		expect(screen.queryByLabelText("What you can do here")).not.toBeInTheDocument();
		expect(screen.queryByLabelText("Getting started")).not.toBeInTheDocument();
		expect(
			screen.queryByText(/Describe an outcome and press Delegate/),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "How it works" })).toBeInTheDocument();
		const examples = screen.getByLabelText("Example delegations");
		expect(examples).toBeInTheDocument();
		expect(examples).toHaveClass("master-examples");
		expect(within(examples).getByRole("button", { name: "Audit & fix" })).toHaveClass(
			"master-example-chip",
		);
		expect(within(examples).getByRole("button", { name: "Split the release" })).toBeInTheDocument();
		expect(
			within(examples).getByRole("button", { name: "What needs attention" }),
		).toBeInTheDocument();
		expect(screen.getByTestId("master-empty")).toBeInTheDocument();
	});

	it("example chips seed the full prompt into the composer callback", async () => {
		const user = userEvent.setup();
		const onExample = vi.fn();
		render(<MasterEmpty onExample={onExample} />);

		const chip = screen.getByRole("button", { name: "Audit & fix" });
		expect(chip).toHaveAttribute(
			"title",
			"Audit this project and delegate independent fixes.",
		);
		await user.click(chip);
		expect(onExample).toHaveBeenCalledTimes(1);
		expect(onExample).toHaveBeenCalledWith(
			"Audit this project and delegate independent fixes.",
		);
	});

	it("opens How it works and dismisses via Got it, Esc, and scrim", async () => {
		const user = userEvent.setup();
		render(<MasterEmpty onExample={vi.fn()} />);

		const trigger = screen.getByRole("button", { name: "How it works" });
		await user.click(trigger);

		const dialog = screen.getByRole("dialog", { name: "How Master works" });
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
		fireEvent.click(screen.getByTestId("master-empty-help-scrim"));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});
});
