import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Composer, slashCommandAriaLabel } from "./Composer";

const mocks = vi.hoisted(() => ({
	getCommandCatalog: vi.fn(),
	listReferenceFiles: vi.fn(),
	listArtifacts: vi.fn(),
	uploadFile: vi.fn(),
}));

vi.mock("../../api/commands", () => ({
	getCommandCatalog: mocks.getCommandCatalog,
}));

vi.mock("../../api/files", () => ({
	listReferenceFiles: mocks.listReferenceFiles,
	listArtifacts: mocks.listArtifacts,
	uploadFile: mocks.uploadFile,
}));

const referenceFiles = {
	files: [
		{ path: "docs/brief.md" },
		{ path: "assets/logo.png" },
		{ path: "src/app.tsx" },
	],
	truncated: false,
};

const scrollIntoView = vi.fn();
let originalScrollIntoView: typeof HTMLElement.prototype.scrollIntoView | undefined;

function renderComposer() {
	const onSubmit = vi.fn().mockResolvedValue(undefined);
	render(
		<Composer
			token="token"
			slug="alpha"
			textareaLabel="Message"
			promptModes={false}
			onSubmit={onSubmit}
		/>,
	);
	return { onSubmit };
}

describe("Composer project-file references", () => {
	beforeEach(() => {
		originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: scrollIntoView,
		});
		scrollIntoView.mockClear();
		vi.clearAllMocks();
		mocks.getCommandCatalog.mockResolvedValue({ groups: [] });
		mocks.listReferenceFiles.mockResolvedValue(referenceFiles);
		mocks.listArtifacts.mockResolvedValue({ artifacts: [] });
	});

	afterEach(() => {
		if (originalScrollIntoView) {
			Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
				configurable: true,
				value: originalScrollIntoView,
			});
		} else {
			Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
		}
	});

	it("loads project files and selects a non-image with the keyboard without submitting", async () => {
		const user = userEvent.setup();
		const { onSubmit } = renderComposer();
		const textarea = screen.getByRole("textbox", {
			name: "Message",
		}) as HTMLTextAreaElement;

		await waitFor(() =>
			expect(mocks.listReferenceFiles).toHaveBeenCalledWith("token", "alpha"),
		);
		await user.type(textarea, "Review @");
		expect(await screen.findByText("docs/brief.md")).toBeInTheDocument();

		// Moving away and back proves the active option is keyboard-controlled. Enter
		// must accept the reference, not submit the whole composer.
		await user.keyboard("{ArrowDown}{ArrowUp}{Enter}");
		await waitFor(() => {
			expect(textarea).toHaveValue("Review docs/brief.md ");
			expect(textarea.selectionStart).toBe("Review docs/brief.md ".length);
		});
		expect(onSubmit).not.toHaveBeenCalled();

		await user.type(textarea, "and summarize it");
		await user.keyboard("{Enter}");
		await waitFor(() =>
			expect(onSubmit).toHaveBeenCalledWith(
				"Review docs/brief.md and summarize it",
				"chat",
			),
		);
	});

	it("keeps more than four matches in the scrollable list", async () => {
		const files = Array.from({ length: 6 }, (_, index) => ({
			path: `docs/file-${index}.md`,
		}));
		mocks.listReferenceFiles.mockResolvedValue({ files, truncated: false });
		const user = userEvent.setup();
		const { onSubmit } = renderComposer();
		const textarea = screen.getByRole("textbox", {
			name: "Message",
		});

		await waitFor(() => expect(mocks.listReferenceFiles).toHaveBeenCalled());
		await user.type(textarea, "@");

		const list = await screen.findByRole("listbox", {
			name: "Project references",
		});
		expect(list).toHaveClass("mention-results");
		const options = screen.getAllByRole("option");
		expect(options).toHaveLength(6);
		expect(textarea).toHaveAttribute("aria-controls", list.id);

		scrollIntoView.mockClear();
		await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}");
		await waitFor(() => {
			expect(textarea).toHaveAttribute(
				"aria-activedescendant",
				options[4].id,
			);
			expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
			expect(
				scrollIntoView.mock.instances[scrollIntoView.mock.instances.length - 1],
			).toBe(options[4]);
		});

		await user.keyboard("{Enter}");
		expect(textarea).toHaveValue("docs/file-4.md ");
		expect(onSubmit).not.toHaveBeenCalled();
	});

	it("formats an image picked with Tab as a Markdown image reference", async () => {
		const user = userEvent.setup();
		const { onSubmit } = renderComposer();
		const textarea = screen.getByRole("textbox", { name: "Message" });

		await waitFor(() => expect(mocks.listReferenceFiles).toHaveBeenCalled());
		await user.type(textarea, "Restyle @logo");
		expect(await screen.findByText("assets/logo.png")).toBeInTheDocument();

		await user.keyboard("{Tab}");
		expect(textarea).toHaveValue(
			"Restyle ![logo.png](assets/logo.png) ",
		);
		expect(onSubmit).not.toHaveBeenCalled();
	});

	it("replaces a mention at the caret without deleting text after it", async () => {
		const user = userEvent.setup();
		const { onSubmit } = renderComposer();
		const textarea = screen.getByRole("textbox", {
			name: "Message",
		}) as HTMLTextAreaElement;

		await waitFor(() => expect(mocks.listReferenceFiles).toHaveBeenCalled());
		await user.type(textarea, "Compare @app after this");
		const caret = "Compare @app".length;
		textarea.setSelectionRange(caret, caret);
		fireEvent.select(textarea);
		fireEvent.click(textarea);

		expect(await screen.findByText("src/app.tsx")).toBeInTheDocument();
		await user.keyboard("{Tab}");
		expect(textarea.value).toMatch(/^Compare src\/app\.tsx\s+after this$/);
		expect(onSubmit).not.toHaveBeenCalled();
	});

	it("surfaces produced artifacts with a kind badge and inserts their path", async () => {
		mocks.listArtifacts.mockResolvedValue({
			artifacts: [
				{
					path: "artifacts/design/launch",
					title: "Launch post",
					type: "design",
				},
			],
		});
		const user = userEvent.setup();
		const { onSubmit } = renderComposer();
		const textarea = screen.getByRole("textbox", { name: "Message" });

		await waitFor(() => expect(mocks.listArtifacts).toHaveBeenCalled());
		await user.type(textarea, "Open @Launch");
		expect(await screen.findByText("Launch post")).toBeInTheDocument();
		expect(screen.getByText("Design")).toBeInTheDocument();
		expect(screen.getByText("artifacts/design/launch")).toBeInTheDocument();

		const list = screen.getByRole("listbox", { name: "Project references" });
		expect(list).toBeInTheDocument();

		await user.keyboard("{Enter}");
		expect(textarea).toHaveValue("Open artifacts/design/launch ");
		expect(onSubmit).not.toHaveBeenCalled();
	});
});

describe("Composer slash commands", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mocks.getCommandCatalog.mockResolvedValue({
			groups: [
				{
					label: "proxima",
					commands: [
						{
							name: "/help",
							description: "Show Proxima chat commands",
							surface: "proxima",
							unavailableMessage: null,
						},
						{
							name: "/status",
							description: "Show current user/project/runner status",
							surface: "proxima",
							unavailableMessage: null,
						},
					],
				},
			],
		});
		mocks.listReferenceFiles.mockResolvedValue({ files: [], truncated: false });
		mocks.listArtifacts.mockResolvedValue({ artifacts: [] });
	});

	it("spaces slash-command accessible names",
		() => {
			expect(
				slashCommandAriaLabel({
					name: "/help",
					description: "Show Proxima chat commands",
					surface: "proxima",
				}),
			).toBe("/help Show Proxima chat commands (proxima)");
		},
	);

	it("lists commands with readable names and inserts on pick", async () => {
		const user = userEvent.setup();
		renderComposer();
		const textarea = screen.getByRole("textbox", { name: "Message" });

		await waitFor(() => expect(mocks.getCommandCatalog).toHaveBeenCalled());
		await user.type(textarea, "/");

		const list = await screen.findByRole("listbox", { name: "Chat commands" });
		expect(list).toBeInTheDocument();
		const help = screen.getByRole("option", {
			name: "/help Show Proxima chat commands (proxima)",
		});
		expect(help).toBeInTheDocument();
		expect(
			screen.queryByRole("option", {
				name: "/helpShow Proxima chat commandsproxima",
			}),
		).not.toBeInTheDocument();

		// mousedown pick keeps the draft insertion without submitting.
		fireEvent.mouseDown(help);
		expect(textarea).toHaveValue("/help ");
	});
});
