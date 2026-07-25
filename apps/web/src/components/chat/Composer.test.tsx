import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	Composer,
	matchSlashCommands,
	SLASH_COMMAND_LIST_VIEWPORT_ROWS,
	slashCommandAriaLabel,
} from "./Composer";

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

describe("matchSlashCommands", () => {
	const many = [
		{ name: "/help" },
		{ name: "/status" },
		{ name: "/new" },
		{ name: "/session" },
		{ name: "/project" },
		{ name: "/grill-with-docs", skillId: "skills/grill-with-docs" },
		{ name: "/grill-me", skillId: "skills/grill-me" },
		{ name: "/masterplan", skillId: "bundled/masterplan" },
	];

	it("exports a viewport row constant of 4 (not a match hard-cap)", () => {
		expect(SLASH_COMMAND_LIST_VIEWPORT_ROWS).toBe(4);
	});

	it("returns all matches for empty slash, preserving top catalog order", () => {
		const matched = matchSlashCommands(many, "/");
		expect(matched.length).toBeGreaterThan(SLASH_COMMAND_LIST_VIEWPORT_ROWS);
		expect(matched).toHaveLength(many.length);
		expect(matched.map((c) => c.name)).toEqual(many.map((c) => c.name));
	});

	it("does not hard-cap prefix matches at 4", () => {
		const matched = matchSlashCommands(many, "/");
		expect(matched.length).toBeGreaterThan(4);
		expect(matched).toHaveLength(8);
	});

	it("narrows as the user types and keeps every prefix match", () => {
		const gri = matchSlashCommands(many, "/gri");
		// Catalog order among prefix matches (grill-with-docs listed before grill-me).
		expect(gri.map((c) => c.name)).toEqual(["/grill-with-docs", "/grill-me"]);
		expect(gri).toHaveLength(2);

		const manyGri = [
			{ name: "/grill-a", skillId: "a" },
			{ name: "/grill-b", skillId: "b" },
			{ name: "/grill-c", skillId: "c" },
			{ name: "/grill-d", skillId: "d" },
			{ name: "/grill-e", skillId: "e" },
			{ name: "/grill-f", skillId: "f" },
		];
		expect(matchSlashCommands(manyGri, "/gri")).toHaveLength(6);
	});

	it("prefers exact name match first", () => {
		const matched = matchSlashCommands(
			[
				{ name: "/help-extra" },
				{ name: "/help" },
				{ name: "/helper" },
			],
			"/help",
		);
		expect(matched[0]?.name).toBe("/help");
	});

	it("respects the enabled filter before ranking", () => {
		const matched = matchSlashCommands(
			many,
			"/",
			(c) => c.name === "/project" || c.name === "/grill-me",
		);
		expect(matched.map((c) => c.name)).toEqual(["/project", "/grill-me"]);
	});
});

describe("Composer slash commands", () => {
	const scrollIntoView = vi.fn();
	let originalScrollIntoView: typeof HTMLElement.prototype.scrollIntoView | undefined;

	beforeEach(() => {
		originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: scrollIntoView,
		});
		scrollIntoView.mockClear();
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
						{
							name: "/masterplan",
							description: "Turn a product idea into an execution-ready masterplan package",
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

	it("spaces slash-command accessible names and omits default surface",
		() => {
			expect(
				slashCommandAriaLabel({
					name: "/help",
					description: "Show Proxima chat commands",
					surface: "proxima",
				}),
			).toBe("/help Show Proxima chat commands");
			expect(
				slashCommandAriaLabel({
					name: "/shell",
					description: "Run in terminal",
					surface: "terminal-only",
				}),
			).toBe("/shell Run in terminal (terminal-only)");
		},
	);

	it("lists commands with readable names, hides default surface, and inserts on pick", async () => {
		const user = userEvent.setup();
		renderComposer();
		const textarea = screen.getByRole("textbox", { name: "Message" });

		await waitFor(() => expect(mocks.getCommandCatalog).toHaveBeenCalled());
		await user.type(textarea, "/");

		const list = await screen.findByRole("listbox", { name: "Chat commands" });
		expect(list).toBeInTheDocument();
		const help = screen.getByRole("option", {
			name: "/help Show Proxima chat commands",
		});
		expect(help).toBeInTheDocument();
		expect(help.querySelector("em")).toBeNull();
		expect(screen.getByRole("option", {
			name: "/masterplan Turn a product idea into an execution-ready masterplan package",
		})).toBeInTheDocument();
		expect(
			screen.queryByRole("option", {
				name: "/helpShow Proxima chat commandsproxima",
			}),
		).not.toBeInTheDocument();
		expect(screen.queryByText("proxima")).not.toBeInTheDocument();

		// mousedown pick keeps the draft insertion without submitting.
		fireEvent.mouseDown(help);
		expect(textarea).toHaveValue("/help ");
	});

	it("keeps all slash matches in the scrollable viewport (not a hard cap of 4)", async () => {
		const user = userEvent.setup();
		const commands = [
			"/help",
			"/status",
			"/new",
			"/session",
			"/project",
			"/runner",
			"/goal",
			"/skill-alpha",
		].map((name) => ({
			name,
			description: `desc for ${name}`,
			surface: "proxima",
			unavailableMessage: null,
		}));
		mocks.getCommandCatalog.mockResolvedValue({
			groups: [{ label: "proxima", commands }],
		});

		renderComposer();
		const textarea = screen.getByRole("textbox", { name: "Message" });
		await waitFor(() => expect(mocks.getCommandCatalog).toHaveBeenCalled());
		await user.type(textarea, "/");

		const list = await screen.findByRole("listbox", { name: "Chat commands" });
		expect(list).toHaveClass("slash-popover");
		const options = list.querySelectorAll('[role="option"]');
		expect(options.length).toBeGreaterThan(SLASH_COMMAND_LIST_VIEWPORT_ROWS);
		expect(options).toHaveLength(8);
		// Fifth+ catalog entries remain in the DOM; CSS max-height scrolls the list.
		expect(
			screen.getByRole("option", {
				name: "/project desc for /project",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("option", {
				name: "/skill-alpha desc for /skill-alpha",
			}),
		).toBeInTheDocument();

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
		expect(textarea).toHaveValue("/project ");
	});

	it("shows non-default surface labels when present", async () => {
		const user = userEvent.setup();
		mocks.getCommandCatalog.mockResolvedValue({
			groups: [
				{
					label: "terminal",
					commands: [
						{
							name: "/shell",
							description: "Open a shell",
							surface: "terminal-only",
							unavailableMessage: null,
						},
					],
				},
			],
		});
		renderComposer();
		const textarea = screen.getByRole("textbox", { name: "Message" });
		await waitFor(() => expect(mocks.getCommandCatalog).toHaveBeenCalled());
		await user.type(textarea, "/");
		const option = await screen.findByRole("option", {
			name: "/shell Open a shell (terminal-only)",
		});
		expect(option.querySelector("em")?.textContent?.trim()).toBe("terminal-only");
	});
});

describe("Composer review draft handoff", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mocks.getCommandCatalog.mockResolvedValue({ groups: [] });
		mocks.listReferenceFiles.mockResolvedValue({ files: [], truncated: false });
		mocks.listArtifacts.mockResolvedValue({ artifacts: [] });
	});

	it("places artifact feedback into the normal chat composer exactly once", async () => {
		const consumed = vi.fn();
		const onSubmit = vi.fn().mockResolvedValue(undefined);
		render(
			<Composer
				token="token"
				slug="alpha"
				textareaLabel="Message"
				promptModes={false}
				draftSeed="Review feedback for [report](artifacts/report.md):"
				draftSeedNonce={1}
				onDraftSeedConsumed={consumed}
				onSubmit={onSubmit}
			/>,
		);

		expect(await screen.findByRole("textbox", { name: "Message" })).toHaveValue(
			"Review feedback for [report](artifacts/report.md):",
		);
		expect(consumed).toHaveBeenCalledTimes(1);
	});
});

describe("Composer submit CTA grammar", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mocks.getCommandCatalog.mockResolvedValue({ groups: [] });
		mocks.listReferenceFiles.mockResolvedValue({ files: [], truncated: false });
		mocks.listArtifacts.mockResolvedValue({ artifacts: [] });
	});

	it("defaults to Send for Chat-like surfaces", () => {
		render(
			<Composer token="token" slug="alpha" textareaLabel="Message" promptModes={false} onSubmit={vi.fn()} />,
		);
		expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
	});

	it("accepts Delegate for Alpha without changing shell grammar", () => {
		render(
			<Composer
				token="token"
				slug="alpha"
				textareaLabel="Delegate an outcome"
				promptModes={false}
				submitLabel="Delegate"
				onSubmit={vi.fn()}
			/>,
		);
		expect(screen.getByRole("button", { name: "Delegate" })).toBeInTheDocument();
		expect(screen.getByRole("textbox", { name: "Delegate an outcome" })).toBeInTheDocument();
		expect(document.querySelector(".composer")).toBeTruthy();
	});
});
