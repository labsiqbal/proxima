import React from "react";
import { getCommandCatalog, type CatalogCommand } from "../../api/commands";
import {
	applyMention,
	filterMentions,
	matchMention,
	mentionInsertion,
	MentionOptionContent,
	type MentionItem,
} from "../ui/MentionTextarea";
import { uploadFile } from "../../api/files";
import type { PromptMode } from "../../api/runs";
import type { AppFeatures } from "../../types";
import { DEFAULT_FEATURES, isFeatureCommandEnabled } from "../../features";
import { useProjectMentionItems } from "../../hooks/useProjectMentionItems";
import {
	IconPlus,
	IconClose,
	IconDesign,
	IconFile,
	IconNewChat,
	IconSend,
	IconSparkle,
	IconUsers,
} from "../shell/icons";

const isImg = (n: string) => /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i.test(n);

/**
 * Visible row count for the slash popover viewport (~4 rows, then scroll).
 * Not a hard cap on matches — the full ranked list stays in the DOM.
 */
export const SLASH_COMMAND_LIST_VIEWPORT_ROWS = 4;

type SlashMatchable = { name: string };

/**
 * Filter enabled catalog commands by prefix on `draft` and rank lightly.
 * Ranking: exact name match first, otherwise stable catalog order so empty `/`
 * keeps top-of-catalog built-ins (e.g. /help, /masterplan) before long skill lists.
 * Returns every match; the popover CSS viewport (~4 rows) scrolls the rest.
 */
export function matchSlashCommands<T extends SlashMatchable>(
	commands: readonly T[],
	draft: string,
	enabled: (command: T) => boolean = () => true,
): T[] {
	const q = draft.toLowerCase();
	const matched = commands.filter(enabled).filter((c) => c.name.startsWith(q));
	return matched
		.map((c, index) => ({ c, index }))
		.sort((a, b) => {
			const aExact = a.c.name === q ? 0 : 1;
			const bExact = b.c.name === q ? 0 : 1;
			if (aExact !== bExact) return aExact - bExact;
			return a.index - b.index;
		})
		.map(({ c }) => c);
}

/** Default catalog surface — Proxima handles the slash (not terminal-only / ui-owned). */
const DEFAULT_SLASH_SURFACE = "proxima";

/** Spaced accessible name for a slash-command row (avoids "/helpShow…proxima"). */
export function slashCommandAriaLabel(command: {
	name: string;
	description: string;
	surface: string;
}): string {
	const base = `${command.name} ${command.description}`;
	// Omit the default surface from a11y names — it is noise on every skill row.
	if (!command.surface || command.surface === DEFAULT_SLASH_SURFACE) return base;
	return `${base} (${command.surface})`;
}

type Att = { path: string; name: string; img: boolean };
type ModeOption = {
	id: PromptMode;
	label: string;
	title: string;
	icon: React.ComponentType<{ size?: number }>;
};
const MODES: ModeOption[] = [
	{
		id: "chat",
		label: "Normal",
		title: "Send a normal single-agent chat prompt",
		icon: IconNewChat,
	},
	{
		id: "brainstorm",
		label: "Brainstorm",
		title: "Explore options before choosing an answer",
		icon: IconSparkle,
	},
	{
		id: "debate",
		label: "Debate",
		title: "Compare opposing positions before answering",
		icon: IconUsers,
	},
];

export function Composer({
	disabled,
	token,
	slug,
	profileId,
	features = DEFAULT_FEATURES,
	placeholder = "Message your agent in this project…",
	attachIconOnly = false,
	promptModes = true,
	generateKinds,
	combinedActions = false,
	submitIconOnly = false,
	submitLabel = "Send",
	submittingLabel = "Sending…",
	textareaLabel,
	mentionItems,
	draftSeed,
	draftSeedNonce,
	onDraftSeedConsumed,
	onSubmit,
	onCatalog,
}: {
	disabled?: boolean;
	token: string;
	slug?: string;
	/** Active profile — skill slash catalog is per profile/runner selection. */
	profileId?: number | null;
	features?: AppFeatures;
	placeholder?: string;
	attachIconOnly?: boolean;
	// Show the Normal/Brainstorm/Debate mode chips and the Generate dropdown.
	// Studio chats turn this off — they are single-agent
	// scene-editing sessions where neither collaboration modes nor media commands apply.
	promptModes?: boolean;
	generateKinds?: Array<"image" | "design">;
	combinedActions?: boolean;
	submitIconOnly?: boolean;
	submitLabel?: string;
	submittingLabel?: string;
	textareaLabel?: string;
	/** Files the owner can @-mention; typing @ offers them and inserts the path. */
	mentionItems?: MentionItem[];
	draftSeed?: string;
	draftSeedNonce?: number;
	onDraftSeedConsumed?: () => void;
	onSubmit: (text: string, promptMode?: PromptMode) => Promise<void>;
	/** Optional: parent uses skill slash names to route agent-turn commands. */
	onCatalog?: (commands: CatalogCommand[]) => void;
}) {
	const [draft, setDraft] = React.useState("");
	const [mode, setMode] = React.useState<PromptMode>("chat");
	const [genOpen, setGenOpen] = React.useState(false);
	const mediaKinds = generateKinds ?? (promptModes ? ["image", "design"] as const : []);
	const genRef = React.useRef<HTMLDivElement>(null);

	React.useEffect(() => {
		if (!genOpen) return;
		const close = (e: MouseEvent) => {
			if (genRef.current && !genRef.current.contains(e.target as Node)) setGenOpen(false);
		};
		const esc = (e: KeyboardEvent) => {
			if (e.key === "Escape") setGenOpen(false);
		};
		document.addEventListener("mousedown", close);
		document.addEventListener("keydown", esc);
		return () => {
			document.removeEventListener("mousedown", close);
			document.removeEventListener("keydown", esc);
		};
	}, [genOpen]);

	// Prefix the draft with the media command; whatever the user types next is the
	// generation prompt. Swaps an existing media-command prefix instead of stacking.
	const pickGenerate = (command: "/image" | "/design") => {
		setGenOpen(false);
		setDraft((d) => `${command} ${d.replace(/^\/(image|gambar|design|image-studio|design-studio)\b\s*/i, "")}`);
		taRef.current?.focus();
	};
	const [commands, setCommands] = React.useState<CatalogCommand[]>([]);
	const [atts, setAtts] = React.useState<Att[]>([]);
	const [uploading, setUploading] = React.useState(false);
	const [submitting, setSubmitting] = React.useState(false);
	const [uploadError, setUploadError] = React.useState("");
	const taRef = React.useRef<HTMLTextAreaElement>(null);
	const pendingMentionCaret = React.useRef<{ caret: number; forText: string } | null>(null);
	const mentionListRef = React.useRef<HTMLDivElement>(null);
	const slashListRef = React.useRef<HTMLDivElement>(null);
	const mentionListId = React.useId();
	const slashListId = React.useId();
	const fileRef = React.useRef<HTMLInputElement>(null);
	const catalogSeq = React.useRef(0);
	const uploadSeq = React.useRef(0);
	const submitSeq = React.useRef(0);
	const mountedRef = React.useRef(true);
	const slugRef = React.useRef(slug);
	const discoveredMentionItems = useProjectMentionItems(
		token,
		mentionItems === undefined ? slug : undefined,
	);
	const availableMentionItems = mentionItems ?? discoveredMentionItems;
	const [mention, setMention] = React.useState<{
		query: string;
		at: number;
	} | null>(null);
	const [mentionActive, setMentionActive] = React.useState(0);
	const [slashActive, setSlashActive] = React.useState(0);
	const mentionQuery = mention?.query;
	const mentionMatches = React.useMemo(
		() =>
			mentionQuery == null
				? []
				: filterMentions(availableMentionItems, mentionQuery),
		[availableMentionItems, mentionQuery],
	);

	React.useEffect(() => {
		if (!mention) return;
		mentionListRef.current
			?.querySelector<HTMLElement>(
				`[data-mention-index="${mentionActive}"]`,
			)
			?.scrollIntoView?.({ block: "nearest" });
	}, [mentionActive, mentionMatches.length, mentionQuery]);

	const syncMention = (
		element: HTMLTextAreaElement,
		value = element.value,
	) => {
		const caret = element.selectionStart ?? value.length;
		const found = matchMention(value.slice(0, caret));
		setMention(found);
		setMentionActive(0);
	};

	const pickMention = (item: MentionItem) => {
		const element = taRef.current;
		if (!element || !mention) return;
		const caret = element.selectionStart ?? draft.length;
		const applied = applyMention(
			draft,
			caret,
			mention.at,
			mentionInsertion(item),
		);
		// Restore the caret after the controlled re-render commits — and only while
		// the value is still exactly the inserted text (same race guard as MentionTextarea).
		pendingMentionCaret.current = { caret: applied.caret, forText: applied.text };
		setDraft(applied.text);
		setMention(null);
	};

	React.useLayoutEffect(() => {
		const pending = pendingMentionCaret.current;
		const element = taRef.current;
		if (!pending || !element) return;
		pendingMentionCaret.current = null;
		if (element.value !== pending.forText) return;
		element.focus();
		element.setSelectionRange(pending.caret, pending.caret);
	});

	React.useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
			catalogSeq.current += 1;
			uploadSeq.current += 1;
			submitSeq.current += 1;
		};
	}, []);

	React.useEffect(() => {
		if (draftSeedNonce && draftSeed != null) {
			setDraft(draftSeed);
			requestAnimationFrame(() => taRef.current?.focus());
			onDraftSeedConsumed?.();
		}
	}, [draftSeed, draftSeedNonce, onDraftSeedConsumed]);

	React.useEffect(() => {
		if (!token) {
			catalogSeq.current += 1;
			setCommands([]);
			onCatalog?.([]);
			return;
		}
		const seq = ++catalogSeq.current;
		void getCommandCatalog(token, { profileId: profileId ?? undefined })
			.then((c) => {
				if (!mountedRef.current || seq !== catalogSeq.current) return;
				const list = c.groups.flatMap((g) => g.commands);
				setCommands(list);
				onCatalog?.(list);
			})
			.catch(() => undefined);
		return () => {
			if (seq === catalogSeq.current) catalogSeq.current += 1;
		};
		// Refresh when profile/runner selection changes so skill slash list matches.
	}, [token, profileId, onCatalog]);

	React.useEffect(() => {
		slugRef.current = slug;
		uploadSeq.current += 1;
		submitSeq.current += 1;
		setAtts([]);
		setUploadError("");
		setUploading(false);
		setSubmitting(false);
		setMention(null);
		if (fileRef.current) fileRef.current.value = "";
	}, [slug]);

	React.useLayoutEffect(() => {
		const el = taRef.current;
		if (!el) return;
		el.style.height = "auto";
		el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
	}, [draft]);

	async function handleFiles(list: FileList | File[] | null) {
		if (!slug || !list || list.length === 0) return;
		const uploadSlug = slug;
		const seq = ++uploadSeq.current;
		setUploading(true);
		setUploadError("");
		for (const f of Array.from(list)) {
			try {
				const r = await uploadFile(token, uploadSlug, f);
				if (
					!mountedRef.current ||
					seq !== uploadSeq.current ||
					slugRef.current !== uploadSlug
				)
					return;
				setAtts((a) => [
					...a,
					{ path: r.path, name: r.name, img: isImg(r.name) },
				]);
				window.dispatchEvent(new CustomEvent("proxima:files-changed"));
			} catch (err) {
				if (
					mountedRef.current &&
					seq === uploadSeq.current &&
					slugRef.current === uploadSlug
				)
					setUploadError(String(err));
			}
		}
		if (
			mountedRef.current &&
			seq === uploadSeq.current &&
			slugRef.current === uploadSlug
		) {
			setUploading(false);
			if (fileRef.current) fileRef.current.value = "";
		}
	}

	const showPopover =
		draft.startsWith("/") && !draft.startsWith("//") && !draft.includes(" ");
	const commandEnabled = (command: CatalogCommand) => {
		return isFeatureCommandEnabled(command, features);
	};
	const commandMatches = showPopover
		? matchSlashCommands(commands, draft, commandEnabled)
		: [];

	React.useEffect(() => {
		setSlashActive(0);
	}, [draft]);

	React.useEffect(() => {
		if (commandMatches.length === 0) return;
		slashListRef.current
			?.querySelector<HTMLElement>(`[data-slash-index="${slashActive}"]`)
			?.scrollIntoView?.({ block: "nearest" });
	}, [slashActive, commandMatches.length, draft]);

	const pickSlashCommand = (command: CatalogCommand) => {
		setDraft(command.name + " ");
	};

	async function submit(event: React.FormEvent) {
		event.preventDefault();
		const text = draft.trim();
		if ((!text && atts.length === 0) || disabled || uploading || submitting)
			return;
		const seq = ++submitSeq.current;
		const refs = atts.map((a) =>
			a.img ? `![${a.name}](${a.path})` : `[${a.name}](${a.path})`,
		);
		const content = [text, ...refs].filter(Boolean).join("\n\n");
		const submitMode = mode;
		setSubmitting(true);
		setDraft("");
		setMention(null);
		setAtts([]);
		setMode("chat");
		try {
			await onSubmit(content, submitMode);
		} catch {
			if (mountedRef.current && seq === submitSeq.current) {
				setDraft(text);
				setAtts(atts);
			}
		} finally {
			if (mountedRef.current && seq === submitSeq.current) setSubmitting(false);
		}
	}

	return (
		<form
			className="composer"
			onSubmit={submit}
			onDragOver={(e) => {
				if (slug) e.preventDefault();
			}}
			onDrop={(e) => {
				if (slug) {
					e.preventDefault();
					void handleFiles(e.dataTransfer.files);
				}
			}}
		>
			{mention && mentionMatches.length > 0 && (
				<div
					id={mentionListId}
					ref={mentionListRef}
					className="slash-popover mention-results"
					role="listbox"
					aria-label="Project references"
				>
					{mentionMatches.map((item, index) => (
						<button
							type="button"
							key={item.path}
							id={`${mentionListId}-option-${index}`}
							data-mention-index={index}
							role="option"
							aria-selected={index === mentionActive}
							className={index === mentionActive ? "active" : ""}
							onMouseEnter={() => setMentionActive(index)}
							onMouseDown={(e) => {
								e.preventDefault();
								pickMention(item);
							}}
						>
							<MentionOptionContent item={item} />
						</button>
					))}
				</div>
			)}
			{commandMatches.length > 0 && (
				<div
					id={slashListId}
					ref={slashListRef}
					className="slash-popover"
					role="listbox"
					aria-label="Chat commands"
				>
					{commandMatches.map((c, index) => (
						<button
							type="button"
							key={c.name}
							id={`${slashListId}-option-${index}`}
							data-slash-index={index}
							role="option"
							aria-selected={index === slashActive}
							aria-label={slashCommandAriaLabel(c)}
							className={index === slashActive ? "active" : ""}
							onMouseEnter={() => setSlashActive(index)}
							onMouseDown={(e) => {
								// Keep focus in the textarea (same as @-mention pick).
								e.preventDefault();
								pickSlashCommand(c);
							}}
						>
							<strong>{c.name}</strong>
							{/* Leading spaces keep a fallback accessible name readable. */}
							<span> {c.description}</span>
							{/* surface=proxima is the default for catalog rows; hide the noise. */}
							{c.surface && c.surface !== DEFAULT_SLASH_SURFACE ? (
								<em> {c.surface}</em>
							) : null}
						</button>
					))}
				</div>
			)}
			{uploadError && (
				<div className="error-bar">
					{uploadError}
					<button
						type="button"
						className="icon-button"
						aria-label="Dismiss upload error"
						onClick={() => setUploadError("")}
					>
						<IconClose size={12} />
					</button>
				</div>
			)}
			{atts.length > 0 && (
				<div className="composer-atts">
					{atts.map((a, i) => (
						<span className="composer-att" key={i}>
							<IconFile size={13} />
							{a.name}
							<button
								type="button"
								aria-label="Remove"
								onClick={() => setAtts((cur) => cur.filter((_, j) => j !== i))}
							>
								<IconClose size={12} />
							</button>
						</span>
					))}
				</div>
			)}
			<textarea
				ref={taRef}
				name="agent-message"
				rows={1}
				aria-label={textareaLabel}
				placeholder={placeholder}
				value={draft}
				onChange={(e) => {
					setDraft(e.target.value);
					syncMention(e.target, e.target.value);
				}}
				onClick={(e) => syncMention(e.currentTarget)}
				onSelect={(e) => syncMention(e.currentTarget)}
				onBlur={() => window.setTimeout(() => setMention(null), 120)}
				disabled={disabled || submitting}
				aria-autocomplete="list"
				aria-expanded={
					(mention != null && mentionMatches.length > 0) ||
					commandMatches.length > 0
				}
				aria-controls={
					mention && mentionMatches.length > 0
						? mentionListId
						: commandMatches.length > 0
							? slashListId
							: undefined
				}
				aria-activedescendant={
					mention && mentionMatches.length > 0
						? `${mentionListId}-option-${Math.min(mentionActive, mentionMatches.length - 1)}`
						: commandMatches.length > 0
							? `${slashListId}-option-${Math.min(slashActive, commandMatches.length - 1)}`
							: undefined
				}
				onPaste={(e) => {
					const files = [...e.clipboardData.items]
						.filter((i) => i.kind === "file")
						.map((i) => i.getAsFile())
						.filter(Boolean) as File[];
					if (files.length && slug) {
						e.preventDefault();
						void handleFiles(files);
					}
				}}
				onKeyDown={(e) => {
					if (mention && mentionMatches.length > 0) {
						if (e.key === "ArrowDown") {
							e.preventDefault();
							setMentionActive((index) =>
								(index + 1) % mentionMatches.length,
							);
							return;
						}
						if (e.key === "ArrowUp") {
							e.preventDefault();
							setMentionActive((index) =>
								(index + mentionMatches.length - 1) % mentionMatches.length,
							);
							return;
						}
						if (e.key === "Enter" || e.key === "Tab") {
							e.preventDefault();
							pickMention(
								mentionMatches[
									Math.min(mentionActive, mentionMatches.length - 1)
								],
							);
							return;
						}
						if (e.key === "Escape") {
							e.preventDefault();
							setMention(null);
							return;
						}
					}
					if (commandMatches.length > 0) {
						if (e.key === "ArrowDown") {
							e.preventDefault();
							setSlashActive((index) =>
								(index + 1) % commandMatches.length,
							);
							return;
						}
						if (e.key === "ArrowUp") {
							e.preventDefault();
							setSlashActive((index) =>
								(index + commandMatches.length - 1) % commandMatches.length,
							);
							return;
						}
						if (e.key === "Enter" || e.key === "Tab") {
							e.preventDefault();
							pickSlashCommand(
								commandMatches[
									Math.min(slashActive, commandMatches.length - 1)
								],
							);
							return;
						}
					}
					if (e.key === "Enter" && !e.shiftKey) {
						e.preventDefault();
						e.currentTarget.form?.requestSubmit();
					}
				}}
			/>
			<div className="composer-footer">
				<input
					ref={fileRef}
					type="file"
					name="attachments"
					multiple
					hidden
					onChange={(e) => void handleFiles(e.target.files)}
				/>
				{!combinedActions && <button
					type="button"
					className="attach-btn"
					disabled={!slug || uploading || submitting}
					aria-label={uploading ? "Uploading files" : "Attach files"}
					title={
						slug
							? uploading
								? "Uploading files"
								: "Attach files"
							: "Pick a project to attach files"
					}
					onClick={() => fileRef.current?.click()}
				>
					<IconPlus size={16} />
					{!attachIconOnly && (
						<span className="composer-label">
							{uploading ? "Uploading…" : "Attach"}
						</span>
					)}
				</button>}
				{/* Media generate lives with the prompt modes: studio chats (promptModes
				    off) are scene-editing sessions where media commands don't apply. */}
				{(combinedActions || mediaKinds.length > 0) && <div className="composer-gen" ref={genRef}>
					<button
						type="button"
						className="attach-btn"
						disabled={disabled || submitting}
						aria-haspopup="menu"
						aria-expanded={genOpen}
						title={combinedActions ? "Add files, image task, or design task" : "Generate an image (/image) or draft a design (/design)"}
						onClick={() => setGenOpen((o) => !o)}
					>
						{combinedActions ? <IconPlus size={16} /> : <IconSparkle size={15} />}
						{!attachIconOnly && <span className="composer-label">{combinedActions ? "Add" : "Generate"}</span>}
					</button>
					{genOpen && (
						<div className="composer-gen-menu" role="menu">
							{combinedActions && <button type="button" role="menuitem" disabled={!slug || uploading} onClick={() => { setGenOpen(false); fileRef.current?.click(); }}>
								<IconFile size={15} /> Attach files
							</button>}
							{mediaKinds.includes("image") && <button type="button" role="menuitem" onClick={() => pickGenerate("/image")}>
								<IconSparkle size={15} /> Image
								<span className="composer-gen-hint">/image</span>
							</button>}
								{mediaKinds.includes("design") && features.designStudio && <button type="button" role="menuitem" onClick={() => pickGenerate("/design")}>
									<IconDesign size={15} /> Design draft
									<span className="composer-gen-hint">/design</span>
								</button>}
						</div>
					)}
				</div>}
				{promptModes && MODES.length > 1 && <div className="composer-modes" aria-label="Prompt mode">
					{MODES.map((opt) => {
						const Icon = opt.icon;
						return (
							<button
								type="button"
								key={opt.id}
								className={mode === opt.id ? "active" : ""}
								title={opt.title}
								aria-label={opt.label}
								disabled={disabled || submitting}
								onClick={() => setMode(opt.id)}
							>
								<span className="composer-icon" aria-hidden="true">
									<Icon size={15} />
								</span>
								<span className="composer-label">{opt.label}</span>
							</button>
						);
					})}
				</div>}
				<button
					className={`primary-button ${submitIconOnly ? "icon-only" : ""}`}
					disabled={
						disabled ||
						uploading ||
						submitting ||
						(!draft.trim() && atts.length === 0)
					}
					type="submit"
					aria-label={submitting ? submittingLabel : submitLabel}
					title={submitting ? submittingLabel : submitLabel}
				>
					<span className="composer-icon" aria-hidden="true">
						<IconSend size={16} />
					</span>
					{!submitIconOnly && <span className="composer-label">
						{submitting ? submittingLabel : submitLabel}
					</span>}
				</button>
			</div>
		</form>
	);
}
