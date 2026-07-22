import React from "react";
import type {
	ChatMessage,
	RunEvent,
	ActivityItem,
	AppFeatures,
	OutputLink,
	Profile,
} from "../../types";
import { MessageContent } from "./MessageContent";
import { QuestionForm } from "./QuestionForm";
import { splitOnQuestionForms } from "./questionForm";
import { respondPermission } from "../../api/runs";
import { designFromImage, previewUrl } from "../../api/files";
import { ApiError } from "../../api/client";
import { IconSparkle, IconArrowDown } from "../shell/icons";
import { MessageReviewSidecar } from "./MessageReviewSidecar";
import { DEFAULT_FEATURES, studioBridgeAvailability } from "../../features";

const ROLE_LABEL: Record<string, string> = {
	user: "You",
	assistant: "Agent",
	error: "Run error",
	system: "Proxima",
};

// Parse a DB timestamp (treat naive timestamps as UTC) → Date, or null.
const parseTs = (s?: string | null): Date | null => {
	if (!s) return null;
	const d = new Date(
		s.replace(" ", "T") + (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? "" : "Z"),
	);
	return isNaN(d.getTime()) ? null : d;
};
const dayLabel = (d: Date): string => {
	const now = new Date(),
		yest = new Date(Date.now() - 86400000);
	if (d.toDateString() === now.toDateString()) return "Today";
	if (d.toDateString() === yest.toDateString()) return "Yesterday";
	return d.toLocaleDateString(undefined, {
		day: "numeric",
		month: "short",
		...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
	});
};
const timeLabel = (d: Date): string =>
	d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

type ApprovalOption = { optionId: string; name?: string; kind?: string };

// Detect a multiple-choice block at/near the end of an agent message (a) b) c)
// or 1. 2. 3.), since Claude over ACP asks in plain text rather than a tool.
// Returns the option labels so we can render clickable quick-reply buttons.
// Smoothly reveal streamed text. ACP delivers deltas in bursts (a clause at a
// time, with gaps), which reads as choppy. We hold the full text as a target and
// advance a displayed cursor toward it every frame — eased so bursts flow out
// smoothly instead of snapping in. Decouples display cadence from arrival.
function useSmoothReveal(target: string, active: boolean): string {
	const [shown, setShown] = React.useState(0);
	const targetRef = React.useRef(target);
	targetRef.current = target;
	React.useEffect(() => {
		if (!active) {
			setShown(0);
			return;
		}
		let raf = 0;
		const tick = () => {
			setShown((prev) => {
				const t = targetRef.current.length;
				if (prev >= t) return prev;
				// Steady cadence: cap chars/frame so big bursts drain smoothly instead of
				// dumping then stalling. ~6/frame ≈ 360 chars/s; the gentle /12 catch-up
				// keeps backlog flowing. Run completion (active=false) snaps to full.
				const remaining = t - prev;
				return (
					prev +
					Math.min(
						remaining,
						Math.max(2, Math.min(6, Math.ceil(remaining / 12))),
					)
				);
			});
			raf = requestAnimationFrame(tick);
		};
		raf = requestAnimationFrame(tick);
		return () => cancelAnimationFrame(raf);
	}, [active]);
	return active ? target.slice(0, shown) : target;
}

// True when `text` hasn't changed for `ms` while active — i.e. the stream has
// stalled (agent is thinking between steps with nothing currently arriving), so
// we can show a "working" indicator during the otherwise-blank gap.
function useStalled(text: string, ms: number, active: boolean): boolean {
	const [stalled, setStalled] = React.useState(false);
	React.useEffect(() => {
		if (!active) {
			setStalled(false);
			return;
		}
		setStalled(false);
		const id = setTimeout(() => setStalled(true), ms);
		return () => clearTimeout(id);
	}, [text, active, ms]);
	return active && stalled;
}

// Isolated so the per-frame reveal re-renders ONLY this bubble, not the whole
// thread (which would re-run the message list 60×/s and cause stutter).
const StreamingBubble = React.memo(function StreamingBubble({
	target,
	active,
	agentLabel,
	token,
	slug,
}: {
	target: string;
	active: boolean;
	agentLabel: string;
	token?: string;
	slug?: string;
}) {
	const shown = useSmoothReveal(target, active);
	if (!shown) return null;
	// Render as markdown (same as the final message) so there's no plain→formatted
	// snap when the stream hands off to the saved message.
	return (
		<div className="chat-line assistant">
			<strong>{agentLabel}</strong>
			<MessageContent content={shown} token={token} slug={slug} />
		</div>
	);
});

// Whimsical cycling status words (Claude-style "spinner words") shown shimmering
// while the agent reasons, so a working pause reads as alive, not stuck.
const THINK_WORDS = [
	"Thinking",
	"Pondering",
	"Brewing",
	"Percolating",
	"Cogitating",
	"Noodling",
	"Simmering",
	"Mulling",
	"Tinkering",
	"Conjuring",
	"Untangling",
	"Scheming",
];
const ThinkingIndicator = React.memo(function ThinkingIndicator({
	agentLabel,
	showLabel,
}: {
	agentLabel: string;
	showLabel: boolean;
}) {
	const [i, setI] = React.useState(0);
	React.useEffect(() => {
		const id = setInterval(() => setI((x) => x + 1), 2400);
		return () => clearInterval(id);
	}, []);
	return (
		<div className="chat-line pending">
			{showLabel && <strong>{agentLabel}</strong>}
			<span className="shimmer">{THINK_WORDS[i % THINK_WORDS.length]}…</span>
		</div>
	);
});

// Compact shimmer for a collapsed collaboration card that is still working —
// same cycling words as ThinkingIndicator, without the chat-line wrapper.
// `seed` offsets the word cycle so parallel cards don't chant in unison.
const CardThinking = React.memo(function CardThinking({
	seed = 0,
}: {
	seed?: number;
}) {
	const [i, setI] = React.useState(0);
	React.useEffect(() => {
		const id = setInterval(() => setI((x) => x + 1), 2400);
		return () => clearInterval(id);
	}, []);
	return (
		<span className="shimmer">
			{THINK_WORDS[(seed + i) % THINK_WORDS.length]}…
		</span>
	);
});

// "Baked for 6m 9s" — playful elapsed-time label on a finished reply.
const BAKE_VERBS = [
	"Baked",
	"Cooked",
	"Brewed",
	"Crafted",
	"Forged",
	"Simmered",
	"Conjured",
	"Wrangled",
];
const fmtDur = (s: number): string => {
	const m = Math.floor(s / 60);
	return m ? `${m}m ${s % 60}s` : `${s}s`;
};
// Hide the goal-loop sentinel ("GOAL_STATUS: CONTINUE") from the chat — it's a
// control signal for Proxima, not content for the user.
const stripGoalStatus = (s: string): string =>
	s.replace(/\n*GOAL_STATUS:[\s\S]*$/i, "").trimEnd();

// A design-chat reply embeds the whole scene as <design-scene>{…}</design-scene>.
// If such a session ever renders in the main chat (e.g. opened from Home activity),
// strip that block so the raw JSON never leaks — including a still-forming block
// mid-stream (open tag, no close yet). Any prose summary around it is kept.
const stripDesignScene = (s: string): string =>
	s
		.replace(/<design-scene[^>]*>[\s\S]*?<\/design-scene>/gi, "")
		.replace(/<design-scene[^>]*>[\s\S]*$/i, "")
		.trimEnd();
const cleanAssistant = (s: string): string =>
	stripDesignScene(stripGoalStatus(s));

function parseChoices(content: string): string[] {
	const lines = content
		.split("\n")
		.map((l) => l.trim())
		.filter(Boolean);
	const re = /^(?:[-*]\s*)?([a-zA-Z]|\d{1,2})[).]\s+(.{2,90})$/;
	const opts: string[] = [];
	// Walk from the end: collect a trailing run of choice-looking lines.
	for (let i = lines.length - 1; i >= 0; i--) {
		const m = lines[i].match(re);
		if (m) opts.unshift(m[2].trim());
		else if (opts.length) break; // run ended
	}
	// Only treat as choices if there are 2–6 (avoid normal numbered prose/lists).
	return opts.length >= 2 && opts.length <= 6 ? opts : [];
}

// Interactive card: agent asked for permission / a choice. Clicking an option
// sends it back to the waiting agent via the run's permission endpoint.
function ApprovalCard({
	title,
	options,
	runId,
	requestId,
	token,
}: {
	title: string;
	options: ApprovalOption[];
	runId: number;
	requestId: string;
	token?: string;
}) {
	const [chosen, setChosen] = React.useState<string | null>(null);
	const [busy, setBusy] = React.useState(false);
	const [error, setError] = React.useState("");
	const mountedRef = React.useRef(true);
	const actionSeq = React.useRef(0);
	React.useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
			actionSeq.current += 1;
		};
	}, []);
	React.useEffect(() => {
		actionSeq.current += 1;
		setChosen(null);
		setBusy(false);
		setError("");
	}, [runId, requestId]);
	const pick = async (o: ApprovalOption) => {
		if (busy || chosen || !token) return;
		const seq = ++actionSeq.current;
		setBusy(true);
		setError("");
		try {
			await respondPermission(token, runId, requestId, o.optionId);
			if (mountedRef.current && seq === actionSeq.current)
				setChosen(o.name || o.optionId);
		} catch (e) {
			if (mountedRef.current && seq === actionSeq.current) {
				setError(
					e instanceof ApiError && e.status === 409
						? "This approval request is no longer active."
						: e instanceof Error
							? e.message
							: String(e),
				);
				setBusy(false);
			}
		}
	};
	const variant = (k?: string) =>
		k?.startsWith("allow")
			? "primary"
			: k?.startsWith("reject")
				? "danger"
				: "";
	return (
		<div
			className={`approval-card enter ${chosen ? "answered" : ""}`}
			role="group"
			aria-label={title}
		>
			<div className="approval-kicker">Approval request</div>
			<div className="approval-title">{title}</div>
			{chosen ? (
				<div className="approval-chosen">
					<span>✓</span>
					{chosen}
				</div>
			) : (
				<div className="approval-options">
					{options.map((o) => (
						<button
							key={o.optionId}
							type="button"
							className={`approval-btn ${variant(o.kind)}`}
							disabled={busy || !token}
							onClick={() => void pick(o)}
						>
							{busy ? "Sending..." : o.name || o.optionId}
						</button>
					))}
				</div>
			)}
			{error && <div className="approval-error">{error}</div>}
		</div>
	);
}

// Persisted tool/subagent activity for a finished reply — collapsed by default so
// the swarm's work stays inspectable without cluttering the thread. 'Task' items
// are subagent spawns and get highlighted.
function ActivityPanel({ items }: { items: ActivityItem[] }) {
	const [open, setOpen] = React.useState(false);
	const subagents = items.filter((i) => i.subagent).length;
	const summary =
		subagents > 0
			? `${subagents} subagent${subagents > 1 ? "s" : ""} · ${items.length} step${items.length > 1 ? "s" : ""}`
			: `${items.length} step${items.length > 1 ? "s" : ""}`;
	return (
		<div className={`activity-panel ${open ? "open" : ""}`}>
			<button
				className="activity-toggle"
				onClick={() => setOpen((o) => !o)}
				aria-expanded={open}
			>
				<span className={`activity-caret ${open ? "open" : ""}`}>▸</span>
				<span className="activity-summary">Agent activity</span>
				<span className="activity-count">{summary}</span>
			</button>
			{open && (
				<div className="activity-items">
					{items.map((it, i) => (
						<div
							key={i}
							className={`activity-item ${it.status} ${it.subagent ? "subagent" : ""}`}
						>
							<span className="tool-dot" />
							<span className="activity-title">{it.title}</span>
							{it.subagent && <span className="activity-badge">subagent</span>}
						</div>
					))}
				</div>
			)}
		</div>
	);
}

const outputTypeLabel = (t: string) =>
	t === "design"
		? "Design"
		: t === "video-file"
				? "Video"
				: t === "app"
					? "App"
					: t === "page"
						? "Page"
						: t === "doc"
							? "Document"
							: t === "image"
								? "Image"
								: "File";

const outputHint = (o: OutputLink, features: AppFeatures) =>
	o.type === "design"
		? features.designStudio ? "Editable layered design" : "Layered design artifact"
		: o.type === "video-file"
				? "Rendered video file"
				: o.type === "app"
					? o.command
						? `Runnable app · ${o.command}`
						: "Runnable app"
					: o.type === "page"
						? "HTML page"
						: o.type === "doc"
							? "Document"
							: o.path;

function ResultCards({
	links,
	onOpen,
	token,
	slug,
	features = DEFAULT_FEATURES,
}: {
	links: OutputLink[];
	onOpen?: (link: OutputLink) => void;
	token?: string;
	slug?: string;
	features?: AppFeatures;
}) {
	const [busy, setBusy] = React.useState("");
	const [actionError, setActionError] = React.useState("");
	if (!links.length) return null;
	// Generated media renders right in the thread — no trip to Artifacts needed.
	const projectOf = (link: OutputLink) => link.project_slug || slug;
	const mediaSrc = (link: OutputLink): string => {
		const project = projectOf(link);
		return token && project ? previewUrl(project, link.path) : "";
	};
	// Bridge a generated image into a fresh Design Studio scene (full-bleed layer).
	const toDesignStudio = async (link: OutputLink) => {
		const project = projectOf(link);
		if (!token || !project) return;
		setBusy(`design:${link.path}`);
		setActionError("");
		try {
			const d = await designFromImage(token, project, link.path, link.title);
			onOpen?.({ type: "design", id: d.id, title: d.title, path: d.path, project_slug: project });
		} catch (e) {
			setActionError(String(e));
		} finally {
			setBusy("");
		}
	};
	return (
		<div className="result-cards" aria-label="Created outputs">
			<div className="result-cards-title">Created outputs</div>
				{links.map((link, i) => {
					const src = mediaSrc(link);
					const bridges = studioBridgeAvailability(link.type, features);
					const canEditDesign = bridges.design;
				return (
					<div className="result-item" key={`${link.type}:${link.path}:${i}`}>
						{link.type === "image" && src && (
							<button
								type="button"
								className="result-media"
								onClick={() => onOpen?.(link)}
								disabled={!onOpen}
								title={link.path}
							>
								<img src={src} alt={link.title || link.path} loading="lazy" />
							</button>
						)}
						{link.type === "video-file" && src && (
							<div className="result-media">
								<video src={`${src}#t=0.1`} controls playsInline preload="metadata" />
							</div>
						)}
						<button
							type="button"
							className="result-card"
							onClick={() => onOpen?.(link)}
							disabled={!onOpen}
							title={link.path}
						>
							<span className={`result-badge rt-${link.type}`}>
								{outputTypeLabel(link.type)}
							</span>
							<span className="result-main">
								<strong>{link.title || link.path}</strong>
									<small>{outputHint(link, features)}</small>
							</span>
							<span className="result-open">Open</span>
						</button>
							{canEditDesign &&
								token &&
							projectOf(link) && (
								<div className="result-actions">
									{canEditDesign && (
										<button
											type="button"
											className="ghost-button"
											disabled={busy !== ""}
											onClick={() => void toDesignStudio(link)}
										>
											{busy === `design:${link.path}` ? "Opening…" : "Edit in Design Studio"}
										</button>
									)}
								</div>
							)}
					</div>
				);
			})}
			{actionError && <small className="error-text">{actionError}</small>}
		</div>
	);
}

type CollaborationCard = {
	runId: number;
	parentRunId: number;
	mode: string;
	agentName: string;
	runnerId: string;
	role: string;
	roundLabel: string;
	status: string;
	text: string;
	error?: string;
	order: number;
};

type CollaborationGroup = {
	parentRunId: number;
	mode: string;
	cards: CollaborationCard[];
};

const COLLAB_EVENT_PREFIX = "collaboration.child.";

function buildCollaborationGroups(
	events: RunEvent[],
): Map<number, CollaborationGroup> {
	const groups = new Map<number, CollaborationGroup>();
	const cards = new Map<number, CollaborationCard>();
	for (const event of events) {
		if (!event.type.startsWith(COLLAB_EVENT_PREFIX)) continue;
		const p = event.payload as Record<string, unknown>;
		const parentRunId = Number(p.parent_run_id || 0);
		const runId = Number(p.run_id || event.run_id || 0);
		if (!parentRunId || !runId) continue;
		const mode = String(p.mode || "brainstorm");
		let group = groups.get(parentRunId);
		if (!group) {
			group = { parentRunId, mode, cards: [] };
			groups.set(parentRunId, group);
		}
		let card = cards.get(runId);
		if (!card) {
			card = {
				runId,
				parentRunId,
				mode,
				agentName: String(p.agent_name || "Agent"),
				runnerId: String(p.runner_id || "agent"),
				role: String(p.role || "participant"),
				roundLabel: String(p.round_label || "Agent response"),
				status: String(p.status || "queued"),
				text: "",
				order: event.id,
			};
			cards.set(runId, card);
			group.cards.push(card);
		}
		card.agentName = String(p.agent_name || card.agentName);
		card.runnerId = String(p.runner_id || card.runnerId);
		card.role = String(p.role || card.role);
		card.roundLabel = String(p.round_label || card.roundLabel);
		card.status = String(p.status || card.status);
		if (event.type === "collaboration.child.delta") {
			card.status = "running";
			card.text += String(p.text || "");
		} else if (event.type === "collaboration.child.completed") {
			card.status = "done";
			card.text = String(p.text || card.text);
		} else if (event.type === "collaboration.child.failed") {
			card.status = "failed";
			card.error = String(p.error || "Agent failed");
		} else if (event.type === "collaboration.child.cancelled") {
			card.status = "cancelled";
		}
	}
	return groups;
}

function renderableCollaborationCards(
	group?: CollaborationGroup,
): CollaborationCard[] {
	// Synthesis is delivered as the final chat message (both modes), so its
	// card would be a duplicate.
	return (group?.cards || [])
		.filter((card) => card.role !== "synthesis")
		.sort((a, b) => a.order - b.order);
}

/** Head control label for a collab card - never includes body text. */
export function collabCardAriaLabel(
	card: { agentName: string; roundLabel: string; status: string },
	closed: boolean,
): string {
	const action = closed ? "Expand" : "Collapse";
	return `${card.agentName}, ${card.roundLabel}, ${card.status}. ${action}`;
}

/**
 * Drop leading runner banners/skills dumps (Pi often prefixes the real answer
 * with `pi v… --- ## Skills - /path …`). Keeps collab previews scannable and
 * matches server-side strip_runner_preamble for already-stored polluted rows.
 */
export function stripRunnerPreamble(text: string): string {
	if (!text) return "";
	let cleaned = text.trim();
	const piBanner = /^\s*pi\s+v[\d.]+\b[\s\S]*?(?=##\s+(?!skills\b))/i;
	const skillsHeading = /^\s*##\s+skills\b[\s\S]*?(?=##\s+(?!skills\b)|$)/i;
	const updateNotice = /^\s*New version available:.*(?:\n|$)/gim;
	for (let i = 0; i < 3; i += 1) {
		const next = cleaned
			.replace(piBanner, "")
			.replace(skillsHeading, "")
			.replace(updateNotice, "")
			.replace(/^[ \t\r\n-]+/, "");
		if (next === cleaned) break;
		cleaned = next;
	}
	return cleaned.trim();
}

function CollaborationCards({
	group,
	token,
	slug,
}: {
	group?: CollaborationGroup;
	token?: string;
	slug?: string;
}) {
	const cards = renderableCollaborationCards(group);
	// Debate reads as a conversation: alternate cards left/right per speaker
	// (first-seen agent = left, opponent = right).
	const debateSide = new Map<number, string>();
	if (group?.mode === "debate") {
		const agentSides = new Map<string, number>();
		for (const card of cards) {
			const key = `${card.agentName}|${card.runnerId}`;
			if (!agentSides.has(key)) agentSides.set(key, agentSides.size);
			debateSide.set(
				card.runId,
				(agentSides.get(key) || 0) % 2 ? "side-right" : "side-left",
			);
		}
	}
	// Brainstorm and debate cards share one behavior (clickable, collapse to a
	// 2-line preview once every agent finished) so the UI stays familiar.
	const isCollapsibleMode =
		group?.mode === "brainstorm" || group?.mode === "debate";
	// Cards stay collapsed by default — including while streaming (a compact
	// shimmer shows live work instead of an ever-growing expanded card). The
	// user can expand any card at any time; expanded ones are never re-closed.
	const cardIdsKey = cards.map((card) => card.runId).join("|");
	const [collapsed, setCollapsed] = React.useState<Set<number>>(() =>
		isCollapsibleMode ? new Set(cards.map((card) => card.runId)) : new Set(),
	);
	const [touched, setTouched] = React.useState<Set<number>>(() => new Set());
	React.useEffect(() => {
		if (!isCollapsibleMode || !cardIdsKey) return;
		const runIds = cardIdsKey.split("|").map((id) => Number(id));
		setCollapsed((current) => {
			const next = new Set(current);
			let changed = false;
			for (const runId of runIds) {
				if (!touched.has(runId) && !next.has(runId)) {
					next.add(runId);
					changed = true;
				}
			}
			return changed ? next : current;
		});
	}, [cardIdsKey, isCollapsibleMode, touched]);
	if (!group || cards.length === 0) return null;
	const title = group.mode === "debate" ? "Debate rounds" : "Brainstorm agents";
	const toggle = (runId: number) => {
		setTouched((current) => {
			const next = new Set(current);
			next.add(runId);
			return next;
		});
		setCollapsed((current) => {
			const isClosed = current.has(runId);
			if (isCollapsibleMode && isClosed) {
				const next = new Set(cards.map((card) => card.runId));
				next.delete(runId);
				return next;
			}
			const next = new Set(current);
			if (isClosed) next.delete(runId);
			else next.add(runId);
			return next;
		});
	};
	return (
		<div className={`collab-cards ${group.mode}`}>
			<div className="collab-cards-title">{title}</div>
			<div className="collab-card-grid">
				{cards.map((card) => {
					const closed = collapsed.has(card.runId);
					const isBrainstormCard = isCollapsibleMode;
					const bodyText = stripRunnerPreamble(card.text || "");
					const preview = (bodyText || card.error || "Waiting for this agent…")
						.replace(/\s+/g, " ")
						.trim();
					const cardClass = `collab-card ${card.status} ${closed ? "collapsed" : ""} ${isBrainstormCard ? "clickable" : ""} ${debateSide.get(card.runId) || ""}`;
					// Mouse users can still click the collapsed preview body; keyboard
					// focus stays on the head button (the only real control).
					const toggleOnClick = (event: React.MouseEvent<HTMLDivElement>) => {
						if (!isBrainstormCard) return;
						const target = event.target as HTMLElement;
						if (target.closest("a, button, input, textarea, select")) return;
						toggle(card.runId);
					};
					const busy = ["queued", "running"].includes(card.status);
					const content = closed ? (
						<div className="collab-card-preview">
							{busy ? (
							<CardThinking seed={card.runId} />
						) : (
							preview || "No output yet."
						)}
						</div>
					) : bodyText.trim() ? (
						<MessageContent content={bodyText} token={token} slug={slug} />
					) : card.error ? (
						<div className="collab-card-error">{card.error}</div>
					) : (
						<div className="collab-card-empty">Waiting for this agent…</div>
					);
					const headLabel = collabCardAriaLabel(card, closed);
					return (
						<div
							key={card.runId}
							className={cardClass}
							onClick={toggleOnClick}
						>
							{/* Head is the real control so body text never becomes the name. */}
							<button
								className="collab-card-head"
								type="button"
								onClick={(event) => {
									event.stopPropagation();
									toggle(card.runId);
								}}
								aria-expanded={!closed}
								aria-label={headLabel}
							>
								{!isBrainstormCard && (
									<span className="collab-card-caret" aria-hidden="true">
										{closed ? "▸" : "▾"}
									</span>
								)}
								<span className="collab-card-title">
									<strong>{card.agentName}</strong>
									{/* Leading space keeps fallback names from reading as "DefaultIdea lane 1". */}
									<span> {card.roundLabel}</span>
								</span>
								<span className="collab-card-controls">
									<em>{card.status}</em>
									{isBrainstormCard && (
										<span className="collab-card-action" aria-hidden="true">
											{closed ? "Expand" : "Collapse"}
										</span>
									)}
								</span>
							</button>
							{!isBrainstormCard && (
								<div className="collab-card-meta">{card.runnerId}</div>
							)}
							<div className="collab-card-body">{content}</div>
						</div>
					);
				})}
			</div>
		</div>
	);
}

export function ChatThread({
	messages,
	events,
	pendingRunId,
	token,
	slug,
	agentName,
	profiles,
	onQuickReply,
	onOpenOutput,
	onMessageUpdated,
	features = DEFAULT_FEATURES,
}: {
	messages: ChatMessage[];
	events: RunEvent[];
	pendingRunId?: number | null;
	token?: string;
	slug?: string;
	agentName?: string;
	profiles?: Profile[];
	onQuickReply?: (text: string) => void;
	onOpenOutput?: (link: OutputLink) => void;
	onMessageUpdated?: (messageId: number, content: string) => void;
	features?: AppFeatures;
}) {
	// Label = recorded author (username for people, profile/agent name for the
	// agent); falls back to the live agent name or the generic role label.
	const labelFor = (m: ChatMessage) =>
		m.author ||
		(m.role === "assistant"
			? agentName || "Agent"
			: ROLE_LABEL[m.role] || "Proxima");
	const agentLabel = agentName || "Agent";
	// The live streaming bubble shows while a run is pending. The owner clears
	// pendingRunId only AFTER reloading the stored message, so the live text never
	// double-renders with (or vanishes before) the saved message (anti-flicker).
	const live = !!pendingRunId;
	const streaming = live
		? events
				.filter((e) => e.type === "message.delta" && e.run_id === pendingRunId)
				.map((e) => String(e.payload.text || ""))
				.join("")
		: "";
	// Prose to stream (everything before a forming <question-form>), revealed smoothly.
	const formCut = streaming.indexOf("<question-form");
	const prose = cleanAssistant(
		formCut >= 0 ? streaming.slice(0, formCut).trimEnd() : streaming,
	);
	const waiting = live && !streaming;
	const stalled = useStalled(streaming, 700, live);
	// Stable ref so memoized message elements don't invalidate each render.
	const onQuickReplyRef = React.useRef(onQuickReply);
	onQuickReplyRef.current = onQuickReply;
	const stableReply = React.useCallback(
		(t: string) => onQuickReplyRef.current?.(t),
		[],
	);

	// Compact tool-activity cards for the live run (from ACP tool.start/tool.complete).
	const tools: { id: string; title: string; status: string }[] = [];
	if (live) {
		const byId = new Map<
			string,
			{ id: string; title: string; status: string }
		>();
		for (const e of events) {
			if (e.run_id !== pendingRunId) continue;
			if (e.type === "tool.start") {
				const id = String(e.payload.id ?? e.id);
				byId.set(id, {
					id,
					title: String(e.payload.title || "tool"),
					status: "running",
				});
			} else if (e.type === "tool.complete") {
				const id = String(e.payload.id ?? "");
				const card = byId.get(id);
				if (card) card.status = String(e.payload.status || "completed");
			}
		}
		tools.push(...byId.values());
	}
	// Show a "thinking" indicator while the run is live but momentarily producing
	// nothing — before the first token, or in the gap after a tool finishes while
	// the agent reasons about its next step (no running tool, stream stalled).
	const hasRunningTool = tools.some((t) => t.status === "running");

	// Interactive approval/question cards for the live run.
	const approvals: {
		requestId: string;
		title: string;
		options: ApprovalOption[];
	}[] = [];
	if (live) {
		const byId = new Map<
			string,
			{ requestId: string; title: string; options: ApprovalOption[] }
		>();
		for (const e of events) {
			if (e.run_id !== pendingRunId || e.type !== "approval.request") continue;
			const requestId = String(e.payload.request_id);
			byId.set(requestId, {
				requestId,
				title: String(e.payload.title || "Permission required"),
				options: (e.payload.options || []) as ApprovalOption[],
			});
		}
		approvals.push(...byId.values());
	}

	const collaborationGroups = React.useMemo(
		() => buildCollaborationGroups(events),
		[events],
	);
	const liveCollaborationGroup = pendingRunId
		? collaborationGroups.get(pendingRunId)
		: undefined;
	// Suppress the bubble/thinking only while cards are actively working (their
	// shimmer covers it). Once every card is done, the synthesis phase streams
	// into the parent bubble like a normal reply — with the thinking indicator
	// filling any silent gap before/between deltas.
	const hasBusyCollaborationCards = renderableCollaborationCards(
		liveCollaborationGroup,
	).some((card) => ["queued", "running"].includes(card.status));
	const showThinking =
		live &&
		!hasRunningTool &&
		!hasBusyCollaborationCards &&
		(waiting || stalled);

	const scrollRef = React.useRef<HTMLDivElement>(null);
	const pinnedRef = React.useRef(true);
	const [atBottom, setAtBottom] = React.useState(true);

	const onScroll = () => {
		const el = scrollRef.current;
		if (!el) return;
		const pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
		pinnedRef.current = pinned;
		setAtBottom(pinned);
	};

	// Follow new content only when the user is already near the bottom.
	React.useLayoutEffect(() => {
		if (pinnedRef.current && scrollRef.current)
			scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
	}, [messages, streaming, waiting, tools.length, collaborationGroups]);

	const scrollToBottom = () => {
		const el = scrollRef.current;
		if (el) {
			el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
			pinnedRef.current = true;
			setAtBottom(true);
		}
	};

	const empty = messages.length === 0 && !live;

	// Memoized so the message list isn't rebuilt on every streaming tick — only
	// when messages / live actually change. Keeps streaming light.
	const messageEls = React.useMemo(
		() =>
			messages.map((message, index) => {
				const ts = parseTs(message.created_at);
				const prevTs =
					index > 0 ? parseTs(messages[index - 1].created_at) : null;
				const showDay =
					ts && (!prevTs || ts.toDateString() !== prevTs.toDateString());
				const messageCollaboration =
					message.role === "assistant" && message.run_id
						? collaborationGroups.get(message.run_id)
						: undefined;
				return (
					<React.Fragment key={message.id ?? index}>
						{showDay && (
							<div className="chat-day-sep">
								<span>{dayLabel(ts!)}</span>
							</div>
						)}
						{messageCollaboration && (
							<CollaborationCards
								group={messageCollaboration}
								token={token}
								slug={slug}
							/>
						)}
						{/* No entrance animation on assistant messages — they stream in (or load from
          history), so re-animating on stream→saved handoff causes an end-of-reply flicker. */}
						<div
							className={`chat-line ${message.role}${message.role === "assistant" ? "" : " enter"}`}
						>
							<strong>
								{labelFor(message)}
								{/* Leading space keeps screen readers from reading "owner04:16 AM". */}
								{ts && <span className="chat-time"> {timeLabel(ts)}</span>}
							</strong>
							{message.role === "assistant" &&
							message.content.includes("<question-form") ? (
								splitOnQuestionForms(message.content).map((seg, si) =>
									seg.kind === "form" ? (
										<QuestionForm
											key={si}
											form={seg.form}
											disabled={live || index !== messages.length - 1}
											onSubmit={stableReply}
										/>
									) : (
										cleanAssistant(seg.text).trim() && (
											<MessageContent
												key={si}
												content={cleanAssistant(seg.text)}
												token={token}
												slug={slug}
											/>
										)
									),
								)
							) : (
								<MessageContent
									content={
										message.role === "assistant"
											? cleanAssistant(message.content)
											: message.content
									}
									token={token}
									slug={slug}
								/>
							)}
							{message.role === "assistant" &&
								message.output_links &&
								message.output_links.length > 0 && (
									<ResultCards
										links={message.output_links}
										onOpen={onOpenOutput}
										token={token}
										slug={slug}
										features={features}
									/>
								)}
							{message.role === "assistant" &&
								message.activity &&
								message.activity.length > 0 && (
									<ActivityPanel items={message.activity} />
								)}
							{message.role === "assistant" && message.duration_s ? (
								<div className="msg-duration">
									{BAKE_VERBS[(message.id || 0) % BAKE_VERBS.length]} for{" "}
									{fmtDur(message.duration_s)}
								</div>
							) : null}
							{message.role === "assistant" && message.id && (
								<MessageReviewSidecar
									token={token}
									message={message}
									events={events}
									tokenSlug={slug}
									profiles={profiles}
									onMessageUpdated={onMessageUpdated}
								/>
							)}
						</div>
					</React.Fragment>
				);
			}),
		[
			messages,
			live,
			token,
			slug,
			stableReply,
			onOpenOutput,
			onMessageUpdated,
			profiles,
			events,
			collaborationGroups,
		],
	);

	return (
		<div className="thread" ref={scrollRef} onScroll={onScroll}>
			<div className="chat-log">
				{empty && (
					<div className="chat-empty">
						<div className="chat-empty-mark">
							<IconSparkle size={30} />
						</div>
						<h3>Start a conversation</h3>
						<p>
							Ask your agent anything in this project. Type <code>/</code> for
							commands.
						</p>
					</div>
				)}
				{messageEls}
				{(tools.length > 0 ||
					(live && streaming.includes("<question-form"))) && (
					<div className="tool-cards enter">
						{tools.map((t) => (
							<div key={t.id} className={`tool-card ${t.status}`}>
								<span className="tool-dot" />
								{t.title}
							</div>
						))}
						{live && streaming.includes("<question-form") && (
							<div className="tool-card running">
								<span className="tool-dot" />
								Creating interactive form…
							</div>
						)}
					</div>
				)}
				{liveCollaborationGroup && (
					<CollaborationCards
						group={liveCollaborationGroup}
						token={token}
						slug={slug}
					/>
				)}
				{approvals.map((a) => (
					<ApprovalCard
						key={`${pendingRunId}:${a.requestId}`}
						title={a.title}
						options={a.options}
						runId={pendingRunId as number}
						requestId={a.requestId}
						token={token}
					/>
				))}
				{(() => {
					// Quick-reply buttons: when the agent finished asking (not live) and its
					// last message ends in a choice list, let the user click instead of type.
					if (live || !onQuickReply || messages.length === 0) return null;
					const last = messages[messages.length - 1];
					if (last.role !== "assistant") return null;
					const choices = parseChoices(last.content);
					if (!choices.length) return null;
					return (
						<div className="quick-replies enter">
							{choices.map((c, i) => (
								<button
									key={i}
									className="quick-reply-btn"
									onClick={() => onQuickReply(c)}
								>
									{c}
								</button>
							))}
						</div>
					);
				})()}
				<StreamingBubble
					target={hasBusyCollaborationCards ? "" : prose}
					active={live}
					agentLabel={agentLabel}
					token={token}
					slug={slug}
				/>
				{showThinking && (
					<ThinkingIndicator agentLabel={agentLabel} showLabel={!prose} />
				)}
			</div>
			{!atBottom && (
				<button
					className="scroll-bottom"
					onClick={scrollToBottom}
					aria-label="Scroll to latest"
					title="Scroll to latest"
				>
					<IconArrowDown size={18} />
				</button>
			)}
		</div>
	);
}
