import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CompactTeachingEmpty } from "./CompactTeachingEmpty";

const baseProps = {
	title: "Delegate an outcome",
	lead: "Dispatch durable jobs; Chat stays hands-on.",
	hints: [
		{ label: "jobs", hint: "Watch the active queue on the side rail" },
		{ label: "needs you", hint: "Reviews and questions collect on the side rail" },
	],
	helpTitle: "How Alpha works",
	helpLead: "Alpha breaks outcomes into durable jobs and returns decisions here.",
	capabilities: [
		"Describe an outcome and press Delegate",
		"Watch the active queue and Needs-you list",
		"Open jobs into Tasks for review",
	],
	steps: [
		"Pick or confirm the shell project if files matter",
		"Write the outcome and constraints",
		"Press Delegate — leave anytime; return to the same desk",
	],
	testId: "alpha-empty",
};

describe("CompactTeachingEmpty", () => {
	it("renders a compact default: title + lead, no capability wall or numbered tutorial", () => {
		render(<CompactTeachingEmpty {...baseProps} />);

		expect(
			screen.getByRole("heading", { name: "Delegate an outcome" }),
		).toBeInTheDocument();
		expect(screen.getByText(/Dispatch durable jobs/)).toBeInTheDocument();

		expect(screen.queryByLabelText("What you can do here")).not.toBeInTheDocument();
		expect(screen.queryByLabelText("Getting started")).not.toBeInTheDocument();
		expect(screen.queryByText(/Describe an outcome and press Delegate/)).not.toBeInTheDocument();
		expect(screen.queryByText(/Write the outcome and constraints/)).not.toBeInTheDocument();

		expect(screen.getByLabelText(/Watch the active queue/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/Reviews and questions/i)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "How it works" })).toBeInTheDocument();
	});

	it("opens a small help dialog with fuller copy and closes via Got it, Esc, and scrim", async () => {
		const user = userEvent.setup();
		render(<CompactTeachingEmpty {...baseProps} />);

		const trigger = screen.getByRole("button", { name: "How it works" });
		await user.click(trigger);

		const dialog = screen.getByRole("dialog", { name: "How Alpha works" });
		expect(dialog).toBeInTheDocument();
		expect(within(dialog).getByLabelText("What you can do here")).toBeInTheDocument();
		expect(within(dialog).getByLabelText("Getting started")).toBeInTheDocument();
		expect(
			within(dialog).getByText(/Describe an outcome and press Delegate/),
		).toBeInTheDocument();
		expect(within(dialog).getByText(/Write the outcome and constraints/)).toBeInTheDocument();

		await user.click(within(dialog).getByRole("button", { name: "Got it" }));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		expect(screen.getByRole("dialog")).toBeInTheDocument();
		await user.keyboard("{Escape}");
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		fireEvent.click(screen.getByTestId("alpha-empty-help-scrim"));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});

	it("closes via the dialog close control", async () => {
		const user = userEvent.setup();
		render(<CompactTeachingEmpty {...baseProps} />);
		await user.click(screen.getByRole("button", { name: "How it works" }));
		await user.click(screen.getByRole("button", { name: "Close" }));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});

	it("renders children under the help trigger (examples stay on the default surface)", () => {
		render(
			<CompactTeachingEmpty {...baseProps}>
				<button type="button">Example chip</button>
			</CompactTeachingEmpty>,
		);
		expect(screen.getByRole("button", { name: "Example chip" })).toBeInTheDocument();
		expect(screen.queryByLabelText("What you can do here")).not.toBeInTheDocument();
	});
});
