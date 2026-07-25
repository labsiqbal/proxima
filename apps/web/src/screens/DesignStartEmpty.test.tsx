import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DesignStartTeaching } from "./DesignStudio";

describe("Design Studio start empty", () => {
	it("renders compact default without project name, caps list, or numbered steps", () => {
		render(<DesignStartTeaching />);

		expect(
			screen.getByRole("heading", { name: "What do you want to make?" }),
		).toBeInTheDocument();
		expect(screen.getByText(/Describe a brief below/)).toBeInTheDocument();

		// Shell switcher owns project context - no "Designing in …" dump.
		expect(screen.queryByText(/Designing in/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/owner \(personal\)/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/saved to this project/i)).not.toBeInTheDocument();

		expect(screen.queryByLabelText("What you can do here")).not.toBeInTheDocument();
		expect(screen.queryByLabelText("Getting started")).not.toBeInTheDocument();
		expect(
			screen.queryByText(/Generate graphics, decks, and social frames/),
		).not.toBeInTheDocument();
		expect(screen.queryByText(/Write a brief below \(or pick a template\)/)).not.toBeInTheDocument();

		expect(screen.getByLabelText(/Describe mood, layout/i)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "How it works" })).toBeInTheDocument();
		expect(screen.getByTestId("design-start-empty")).toBeInTheDocument();
	});

	it("opens How it works and dismisses via Got it, Esc, and scrim", async () => {
		const user = userEvent.setup();
		render(<DesignStartTeaching />);

		const trigger = screen.getByRole("button", { name: "How it works" });
		await user.click(trigger);

		const dialog = screen.getByRole("dialog", { name: "How Design Studio works" });
		expect(within(dialog).getByLabelText("What you can do here")).toBeInTheDocument();
		expect(within(dialog).getByLabelText("Getting started")).toBeInTheDocument();
		expect(
			within(dialog).getByText(/Generate graphics, decks, and social frames/),
		).toBeInTheDocument();

		await user.click(within(dialog).getByRole("button", { name: "Got it" }));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		await user.keyboard("{Escape}");
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		await waitFor(() => expect(trigger).toHaveFocus());

		await user.click(trigger);
		fireEvent.click(screen.getByTestId("design-start-empty-help-scrim"));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});
});
