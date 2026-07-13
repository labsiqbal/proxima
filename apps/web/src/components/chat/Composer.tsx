import React from "react";
import { getCommandCatalog, type CatalogCommand } from "../../api/commands";
import { uploadFile } from "../../api/files";
import type { PromptMode } from "../../api/runs";
import type { AppFeatures } from "../../types";
import { DEFAULT_FEATURES, isFeatureCommandEnabled } from "../../features";
import {
	IconPlus,
	IconClose,
	IconDesign,
	IconFile,
	IconNewChat,
	IconSend,
	IconSparkle,
	IconUsers,
	IconVideo,
} from "../shell/icons";

const isImg = (n: string) => /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i.test(n);

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
	features = DEFAULT_FEATURES,
	placeholder = "Message your agent in this project…",
	attachIconOnly = false,
	promptModes = true,
	draftSeed,
	draftSeedNonce,
	onSubmit,
}: {
	disabled?: boolean;
	token: string;
	slug?: string;
	features?: AppFeatures;
	placeholder?: string;
	attachIconOnly?: boolean;
	// Show the Normal/Brainstorm/Debate mode chips AND the Generate (/image, /video)
	// dropdown. Studio chats (design/video) turn this off — they are single-agent
	// scene-editing sessions where neither collaboration modes nor media commands apply.
	promptModes?: boolean;
	draftSeed?: string;
	draftSeedNonce?: number;
	onSubmit: (text: string, promptMode?: PromptMode) => Promise<void>;
}) {
	const [draft, setDraft] = React.useState("");
	const [mode, setMode] = React.useState<PromptMode>("chat");
	const [genOpen, setGenOpen] = React.useState(false);
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
	const pickGenerate = (command: "/image" | "/video" | "/design") => {
		setGenOpen(false);
		setDraft((d) => `${command} ${d.replace(/^\/(image|gambar|video-studio|video|design|image-studio|design-studio)\b\s*/i, "")}`);
		taRef.current?.focus();
	};
	const [commands, setCommands] = React.useState<CatalogCommand[]>([]);
	const [atts, setAtts] = React.useState<Att[]>([]);
	const [uploading, setUploading] = React.useState(false);
	const [submitting, setSubmitting] = React.useState(false);
	const [uploadError, setUploadError] = React.useState("");
	const taRef = React.useRef<HTMLTextAreaElement>(null);
	const fileRef = React.useRef<HTMLInputElement>(null);
	const catalogSeq = React.useRef(0);
	const uploadSeq = React.useRef(0);
	const submitSeq = React.useRef(0);
	const mountedRef = React.useRef(true);
	const slugRef = React.useRef(slug);

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
		}
	}, [draftSeed, draftSeedNonce]);

	React.useEffect(() => {
		if (!token) {
			catalogSeq.current += 1;
			setCommands([]);
			return;
		}
		const seq = ++catalogSeq.current;
		void getCommandCatalog(token)
			.then((c) => {
				if (mountedRef.current && seq === catalogSeq.current)
					setCommands(c.groups.flatMap((g) => g.commands));
			})
			.catch(() => undefined);
		return () => {
			if (seq === catalogSeq.current) catalogSeq.current += 1;
		};
	}, [token]);

	React.useEffect(() => {
		slugRef.current = slug;
		uploadSeq.current += 1;
		submitSeq.current += 1;
		setAtts([]);
		setUploadError("");
		setUploading(false);
		setSubmitting(false);
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
	const matches = showPopover
		? commands.filter(commandEnabled).filter((c) => c.name.startsWith(draft.toLowerCase()))
		: [];

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
		setAtts([]);
		setMode("chat");
		try {
			await onSubmit(content, submitMode);
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
			{matches.length > 0 && (
				<div className="slash-popover">
					{matches.map((c) => (
						<button
							type="button"
							key={c.name}
							onClick={() => setDraft(c.name + " ")}
						>
							<strong>{c.name}</strong>
							<span>{c.description}</span>
							<em>{c.surface}</em>
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
				rows={1}
				placeholder={placeholder}
				value={draft}
				onChange={(e) => setDraft(e.target.value)}
				disabled={disabled || submitting}
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
					multiple
					hidden
					onChange={(e) => void handleFiles(e.target.files)}
				/>
				<button
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
				</button>
				{/* Media generate lives with the prompt modes: studio chats (promptModes
				    off) are scene-editing sessions where /image · /video don't apply. */}
				{promptModes && <div className="composer-gen" ref={genRef}>
					<button
						type="button"
						className="attach-btn"
						disabled={disabled || submitting}
						aria-haspopup="menu"
						aria-expanded={genOpen}
							title="Generate media with the selected provider (/image, /video) or draft a design (/design)"
						onClick={() => setGenOpen((o) => !o)}
					>
						<IconSparkle size={15} />
						{!attachIconOnly && <span className="composer-label">Generate</span>}
					</button>
					{genOpen && (
						<div className="composer-gen-menu" role="menu">
							<button type="button" role="menuitem" onClick={() => pickGenerate("/image")}>
								<IconSparkle size={15} /> Image
								<span className="composer-gen-hint">/image</span>
							</button>
								{features.designStudio && <button type="button" role="menuitem" onClick={() => pickGenerate("/design")}>
									<IconDesign size={15} /> Design draft
									<span className="composer-gen-hint">/design</span>
								</button>}
								{features.video && <button type="button" role="menuitem" onClick={() => pickGenerate("/video")}>
									<IconVideo size={15} /> Video
									<span className="composer-gen-hint">/video</span>
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
					className="primary-button"
					disabled={
						disabled ||
						uploading ||
						submitting ||
						(!draft.trim() && atts.length === 0)
					}
					type="submit"
					aria-label={submitting ? "Sending message" : "Send message"}
					title={submitting ? "Sending…" : "Send"}
				>
					<span className="composer-icon" aria-hidden="true">
						<IconSend size={16} />
					</span>
					<span className="composer-label">
						{submitting ? "Sending…" : "Send"}
					</span>
				</button>
			</div>
		</form>
	);
}
