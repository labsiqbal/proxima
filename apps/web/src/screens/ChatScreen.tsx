import React from "react";
import {
	createRun,
	cancelRun,
	listEvents,
	startGoal,
	cancelGoal,
} from "../api/runs";
import type { PromptMode } from "../api/runs";
import { getGoalMaxIter } from "../lib/goal";
import type { GoalState } from "../types";
import {
	createSession,
	listMessages,
	setSessionProfile,
} from "../api/sessions";
import { draftWikiNote, commitWikiNote } from "../api/wiki";
import { useRunStream } from "../hooks/useRunStream";
import type {
	ChatMessage,
	ChatSession,
	GraphWorkflowDraft,
	AppFeatures,
	OutputLink,
	Profile,
	Project,
	RunEvent,
	WikiDraft,
	WorkflowDraft,
} from "../types";
import { ChatThread } from "../components/chat/ChatThread";
import { WikiNotePreview } from "../components/wiki/WikiNotePreview";
import { ConvertToWorkflowButton } from "../components/ConvertToWorkflowButton";
import { Composer } from "../components/chat/Composer";
import { Dropdown } from "../components/ui/Dropdown";
import { IconAgents, IconClose, IconNewChat, IconWiki } from "../components/shell/icons";
import { notify } from "../lib/notify";

const cleanName = (n: string) => n.replace(/\s*\(private\)\s*$/i, "");

/** Header label prefers the open session's project so a desynced shell pick cannot mislabel the chat. */
export function chatHeaderProjectLabel(
	activeSession: ChatSession | null | undefined,
	activeProject: Project | null | undefined,
	projects: Project[],
): string {
	const fromSession = activeSession?.project_slug
		? projects.find((p) => p.slug === activeSession.project_slug)
		: null;
	if (fromSession) return cleanName(fromSession.name);
	if (activeSession?.project_name) return cleanName(activeSession.project_name);
	if (activeProject) return cleanName(activeProject.name);
	return "No project";
}

function localCommandReply(
	name: string,
	props: {
		activeProject: Project | null;
		activeProfile: Profile | null;
		activeSession: ChatSession | null;
	},
): string {
	switch (name) {
		case "/help":
			return "Commands: /new (new chat), /status, /session, /project <name> (switch to another project's chat). Prefix // to send a literal slash message to the agent.";
		case "/status":
			return `Project: ${props.activeProject?.name || "none"} · Agent: ${props.activeProfile?.name || "none"}`;
		case "/session":
			return `Session: ${props.activeSession?.title || "new chat"}`;
		case "/project":
			return `Project: ${props.activeProject?.name || "none"} (${props.activeProject?.slug || "-"}). Type "/project <name>" to switch to another project's chat.`;
		default:
			return "Unknown command.";
	}
}

const defaultRunRecipePrompt = (features: AppFeatures) => {
	const artifactKinds = features.designStudio ? "a design or file" : "an image or file";
	return `Run this entire recipe from step 1 through the final step now as a dry-test. Execute each step in order and produce the real output. If a step asks for ${artifactKinds}, create it instead of only describing it. Finish with a concise summary of each step result.`;
};

export function ChatScreen(props: {
	token: string;
	features: AppFeatures;
	activeProfile: Profile | null;
	activeProject: Project | null;
	activeSession: ChatSession | null;
	profiles: Profile[];
	projects: Project[];
	onActiveProfile: (p: Profile) => void;
	onActiveProject: (p: Project | null) => void;
	onSession: (s: ChatSession) => void;
	onRefresh: () => Promise<void>;
	onNewSession: () => Promise<void>;
	onWorkflowDraft?: (draft: WorkflowDraft) => void;
	onGraphDraft?: (draft: GraphWorkflowDraft) => void;
	onOpenOutput?: (link: OutputLink, origin: ChatSession | null) => void;
	runRecipeNonce?: number;
	runRecipePrompt?: string;
	runRecipeLabel?: string;
	runRecipeInstantResult?: string;
}) {
	const [messages, setMessages] = React.useState<ChatMessage[]>([]);
	const [goal, setGoal] = React.useState<GoalState | null>(null);
	const [localSession, setLocalSession] = React.useState<ChatSession | null>(
		null,
	);
	const [error, setError] = React.useState("");
	const [wikiNotice, setWikiNotice] = React.useState("");
	const [wikiDraft, setWikiDraft] = React.useState<WikiDraft | null>(null);
	const [savingWiki, setSavingWiki] = React.useState(false);
	const seenDraftId = React.useRef(0);
	const justCreated = React.useRef<number | null>(null); // session we lazily created on send — skip the reload-on-change
	const loadSeq = React.useRef(0);
	const actionSeq = React.useRef(0);
	const auxActionSeq = React.useRef(0);
	const mountedRef = React.useRef(true);
	const activeSessionIdRef = React.useRef<number | null>(null);
	const activeSession = localSession || props.activeSession;

	React.useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
			loadSeq.current += 1;
			actionSeq.current += 1;
			auxActionSeq.current += 1;
		};
	}, []);

	React.useEffect(() => {
		activeSessionIdRef.current = activeSession?.id || null;
	}, [activeSession?.id]);

	const onEventRef = React.useRef<(e: RunEvent) => void>(() => {});
	// The shared engine owns the SSE subscription, delta coalescing → events list, and
	// busyRun (+ ref) with reconnect-on-open. This surface layers only its control-event
	// reactions (goal loop, terminal → reload) on top.
	const {
		events,
		setEvents,
		busyRun,
		setBusyRun,
		busyRunRef,
		connected,
		restore,
	} = useRunStream(props.token, activeSession?.id || null, (e) =>
		onEventRef.current(e),
	);

	// Restore THIS chat's own agent when it opens: point the picker at the session's
	// persisted profile, so switching agent in one chat doesn't bleed into the others.
	// Only acts when the session names a different, still-existing profile (never
	// fights a fresh session with no profile yet, nor the user's in-chat switch).
	React.useEffect(() => {
		const pid = activeSession?.profile_id;
		if (!pid || pid === props.activeProfile?.id) return;
		const p = props.profiles.find((profile) => profile.id === pid);
		if (p) props.onActiveProfile(p);
	}, [activeSession?.id, activeSession?.profile_id, props.profiles]);

	// Open the preview when a wiki.draft event arrives on this session's stream.
	// Dedup by events.id (session-monotonic), NOT seq — seq resets per run, so a
	// second Save-to-wiki (its own run) reused seq=2 and was wrongly skipped.
	React.useEffect(() => {
		const ev = [...events]
			.reverse()
			.find((e) => e.type === "wiki.draft" && e.id > seenDraftId.current);
		if (!ev) return;
		seenDraftId.current = ev.id;
		// Only auto-open for a draft the user is actively waiting on (just clicked
		// "Save to wiki"). A historical/uncommitted draft gets REPLAYED by the SSE
		// stream every time the session opens — without this guard it re-pops the
		// modal on every visit (the baseline can lose a race with the replay).
		if (savingWiki) {
			setWikiDraft(ev.payload as unknown as WikiDraft);
			setSavingWiki(false);
		}
	}, [events, savingWiki]);

	// For a workflow iterate/test chat: fold this conversation back into the recipe.
	async function startWikiDraft() {
		if (!activeSession) return;
		const seq = ++auxActionSeq.current;
		setSavingWiki(true);
		setError("");
		try {
			await draftWikiNote(
				props.token,
				activeSession.id,
				props.activeProfile?.id,
			);
		} catch (e) {
			if (mountedRef.current && seq === auxActionSeq.current) {
				setSavingWiki(false);
				setError(String(e));
			}
		}
	}

	const loadMessages = React.useCallback(
		async (sessionId?: number) => {
			if (!mountedRef.current) return;
			const id = sessionId || activeSession?.id;
			const seq = ++loadSeq.current;
			setWikiDraft(null);
			setWikiNotice("");
			setSavingWiki(false); // clean slate: no leftover draft modal on session switch
			if (!id) {
				if (mountedRef.current) {
					setMessages([]);
					setEvents([]);
					setBusyRun(null);
					setGoal(null);
				}
				return;
			}
			// restore() is pure (no state writes); apply under our own staleness guard so a
			// session switched mid-await can't clobber the newly opened one.
			const [body, r] = await Promise.all([
				listMessages(props.token, id),
				restore(id),
			]);
			if (
				!mountedRef.current ||
				seq !== loadSeq.current ||
				activeSessionIdRef.current !== id
			)
				return;
			setMessages(body.messages);
			setEvents(r.events);
			setGoal(body.goal || null);
			// Baseline the wiki-draft watcher to the highest loaded id so an OLD draft event
			// doesn't re-pop the "Save to wiki" modal on every open — only a fresh one should.
			seenDraftId.current = r.events.reduce(
				(m, e) => Math.max(m, e.id),
				seenDraftId.current,
			);
			// Re-attach to an in-flight run so the live stream + thinking indicator resume.
			setBusyRun(r.running ? r.lastRun : null);
		},
		[activeSession?.id, props.token, restore, setEvents, setBusyRun],
	);

	// Control-event reactions only — deltas and the events list are the engine's job.
	const onEvent = (event: RunEvent) => {
		if (event.type === "goal.update") {
			setGoal(
				(prev) =>
					({
						objective: prev?.objective || "",
						iteration: prev?.iteration || 0,
						max: prev?.max || 20,
						...(event.payload as object),
					}) as GoalState,
			);
		}
		// A goal loop auto-enqueues its next turn; follow it live so the stream continues
		// seamlessly across iterations instead of going blank.
		if (
			event.type === "run.queued" &&
			(event.payload as { goal?: boolean })?.goal
		) {
			setBusyRun(event.run_id);
		}
		// Only the ACTIVE run's terminal event clears the loading state. The stream replays
		// old runs' terminal events on reconnect; this guard stops them clobbering a
		// restored/next busyRun (a goal loop may have already moved busyRun to run B).
		if (
			["run.completed", "run.failed", "run.cancelled"].includes(event.type) &&
			event.run_id === busyRunRef.current
		) {
			const completedRun = event.run_id;
			void loadMessages(event.session_id).then(() => {
				if (
					mountedRef.current &&
					activeSessionIdRef.current === event.session_id
				)
					setBusyRun((cur) => (cur === completedRun ? null : cur));
			});
			window.dispatchEvent(new CustomEvent("proxima:files-changed"));
			if (event.type === "run.completed")
				notify("Agent finished", "New reply in chat.");
		}
	};
	React.useEffect(() => {
		onEventRef.current = onEvent;
	});

	React.useEffect(() => {
		// The session just created locally on first send is already loaded + sending —
		// don't reload/reset it (that would race the in-flight run + drop the message).
		if (
			props.activeSession?.id &&
			props.activeSession.id === justCreated.current
		) {
			justCreated.current = null;
			return;
		}
		setLocalSession(null);
		setBusyRun(null);
		const p = loadMessages(props.activeSession?.id);
		const seq = loadSeq.current;
		void p.catch((err) => {
			if (mountedRef.current && seq === loadSeq.current) setError(String(err));
		});
	}, [props.activeSession?.id, loadMessages]);

	async function ensureSession(text: string): Promise<ChatSession> {
		if (activeSession) return activeSession;
		const created = await createSession(props.token, {
			title: text.slice(0, 60),
			project_slug: props.activeProject?.slug || null,
			profile_id: props.activeProfile?.id || null,
		});
		if (!mountedRef.current) return created;
		justCreated.current = created.id;
		activeSessionIdRef.current = created.id;
		setLocalSession(created);
		props.onSession(created);
		return created;
	}

	// Switch to a project: the parent opens that project's most recent chat (or a
	// blank new one). Each project keeps its own conversation history.
	function chooseProject(slug: string) {
		const p = props.projects.find((project) => project.slug === slug) || null;
		if (p) props.onActiveProject(p);
	}

	async function submit(text: string, promptMode: PromptMode = "chat") {
		const seq = ++actionSeq.current;
		setError("");
		try {
			const trimmed = text.trim();
			// Media commands are real prompts — the backend routes them to the selected
			// generation provider (create_run interception), so they must reach it.
				const mediaCommand = /^\/(image|gambar)\b/i.test(trimmed)
					|| (props.features.designStudio && /^\/(design|image-studio|design-studio)\b/i.test(trimmed));
			if (trimmed.startsWith("/") && !trimmed.startsWith("//") && !mediaCommand) {
				const name = trimmed.split(/\s+/)[0].toLowerCase();
				if (name === "/new" || name === "/reset") {
					await props.onNewSession();
					return;
				}
				if (name === "/project") {
					const arg = trimmed.split(/\s+/).slice(1).join(" ").trim();
					if (arg) {
						const target = props.projects.find(
							(p) =>
								p.slug === arg ||
								cleanName(p.name).toLowerCase() === arg.toLowerCase(),
						);
						if (!target) {
							setMessages((current) => [
								...current,
								{
									role: "system",
									content: `No project matches "${arg}". Use the project name or slug.`,
								},
							]);
							return;
						}
						chooseProject(target.slug);
						return;
					}
				}
				if (
					name === "/help" ||
					name === "/status" ||
					name === "/session" ||
					name === "/project"
				) {
					setMessages((current) => [
						...current,
						{ role: "system", content: localCommandReply(name, props) },
					]);
					return;
				}
				if (name === "/model" || name === "/clear" || name === "/tools") {
					setMessages((current) => [
						...current,
						{
							role: "system",
							content: `${name} is managed by Proxima UI, not raw chat.`,
						},
					]);
					return;
				}
				if (name === "/goal") {
					const arg = trimmed.split(/\s+/).slice(1).join(" ").trim();
					if (!arg) {
						setMessages((current) => [
							...current,
							{
								role: "system",
								content:
									"Usage: /goal <objective> — the agent works autonomously across turns until it reports the goal done (or needs your input). Stop anytime.",
							},
						]);
						return;
					}
					const session = await ensureSession(arg);
					if (
						!mountedRef.current ||
						seq !== actionSeq.current ||
						activeSessionIdRef.current !== session.id
					)
						return;
					setMessages((current) => [
						...current,
						{ role: "user", content: `🎯 Goal: ${arg}` },
					]);
					const maxIter = getGoalMaxIter();
					const r = await startGoal(props.token, session.id, {
						objective: arg,
						profile_id: props.activeProfile?.id || null,
						model: props.activeProfile?.default_model || null,
						max_iter: maxIter,
					});
					if (
						!mountedRef.current ||
						seq !== actionSeq.current ||
						activeSessionIdRef.current !== session.id
					)
						return;
					setBusyRun(r.run_id);
					setGoal({
						objective: arg,
						status: "running",
						iteration: 0,
						max: maxIter,
					});
					const eventBody = await listEvents(props.token, session.id);
					if (
						mountedRef.current &&
						seq === actionSeq.current &&
						activeSessionIdRef.current === session.id
					)
						setEvents(eventBody.events);
					await props.onRefresh();
					return;
				}
				setMessages((current) => [
					...current,
					{
						role: "system",
						content: `Unknown command ${name}. Type /help to see available commands.`,
					},
				]);
				return;
			}
			const prompt = trimmed.startsWith("//") ? trimmed.slice(1) : trimmed;
			const session = await ensureSession(prompt);
			if (
				!mountedRef.current ||
				seq !== actionSeq.current ||
				activeSessionIdRef.current !== session.id
			)
				return;
			setMessages((current) => [
				...current,
				{ role: "user", content: prompt },
			]);
			const participantProfileIds =
				promptMode === "chat"
					? undefined
					: props.profiles.map((profile) => profile.id);
			const run = await createRun(props.token, session.id, {
				message: prompt,
				profile_id: props.activeProfile?.id || null,
				participant_profile_ids: participantProfileIds,
				model: props.activeProfile?.default_model || null,
				prompt_mode: promptMode,
			});
			if (
				!mountedRef.current ||
				seq !== actionSeq.current ||
				activeSessionIdRef.current !== session.id
			)
				return;
			if (run.status === "completed") {
				// A media command that finished synchronously — a /image or /design
				// clarify form or a design draft. Its run.completed event already
				// fired before we could subscribe to the stream, so waiting on the stream
				// would leave the composer stuck "Simmering…". Don't set busy; just load
				// the assistant reply (the form / artifact card) directly.
				await loadMessages(session.id);
			} else {
				setBusyRun(run.run_id);
			}
			const eventBody = await listEvents(props.token, session.id);
			if (
				mountedRef.current &&
				seq === actionSeq.current &&
				activeSessionIdRef.current === session.id
			)
				setEvents(eventBody.events);
			await props.onRefresh();
		} catch (err) {
			if (mountedRef.current && seq === actionSeq.current)
				setError(String(err));
			throw err;
		}
	}

	async function runFromStage(
		prompt: string,
		label: string,
		instantResult?: string,
	) {
		const seq = ++actionSeq.current;
		setError("");
		try {
			const session = await ensureSession(label);
			if (
				!mountedRef.current ||
				seq !== actionSeq.current ||
				activeSessionIdRef.current !== session.id
			)
				return;
			setMessages((current) => [...current, { role: "user", content: label }]);
			const run = await createRun(props.token, session.id, {
				message: prompt,
				display_message: label,
				instant_result: instantResult,
				profile_id: props.activeProfile?.id || null,
				model: props.activeProfile?.default_model || null,
			});
			if (
				!mountedRef.current ||
				seq !== actionSeq.current ||
				activeSessionIdRef.current !== session.id
			)
				return;
			setBusyRun(run.run_id);
			const eventBody = await listEvents(props.token, session.id);
			if (
				mountedRef.current &&
				seq === actionSeq.current &&
				activeSessionIdRef.current === session.id
			)
				setEvents(eventBody.events);
			if (mountedRef.current && seq === actionSeq.current)
				props.onSession(session);
			window.dispatchEvent(new CustomEvent("proxima:files-changed"));
		} catch (err) {
			if (mountedRef.current && seq === actionSeq.current)
				setError(String(err));
		}
	}

	// "Run recipe" from the iterate stage: send a dry-run instruction so the agent
	// executes the CURRENT recipe end-to-end (the result then reflects the recipe).
	// Baseline to the CURRENT nonce so re-mounting (switching chats and back) doesn't
	// re-fire a stale dry-run — only a fresh bump after mount triggers it.
	const lastRunNonce = React.useRef(props.runRecipeNonce || 0);
	React.useEffect(() => {
		const n = props.runRecipeNonce || 0;
		if (n > 0 && n !== lastRunNonce.current) {
			lastRunNonce.current = n;
			void runFromStage(
					props.runRecipePrompt || defaultRunRecipePrompt(props.features),
				props.runRecipeLabel || "Run recipe",
				props.runRecipeInstantResult,
			);
		}
	}, [
		props.runRecipeNonce,
		props.runRecipePrompt,
		props.runRecipeLabel,
		props.runRecipeInstantResult,
	]);
	const openOutput = React.useCallback(
		(link: OutputLink) => {
			props.onOpenOutput?.(link, activeSession);
		},
		[props.onOpenOutput, activeSession],
	);

	const updateMessageContent = React.useCallback(
		(messageId: number, content: string) => {
			setMessages((current) =>
				current.map((m) => (m.id === messageId ? { ...m, content } : m)),
			);
		},
		[],
	);

	async function stopGoal() {
		if (!activeSession) return;
		const runId = busyRun;
		const seq = ++actionSeq.current;
		try {
			await cancelGoal(props.token, activeSession.id);
			if (!mountedRef.current || seq !== actionSeq.current) return;
			setGoal((g) => (g ? { ...g, status: "cancelled" } : g));
			if (runId != null) setBusyRun((cur) => (cur === runId ? null : cur));
			await loadMessages(activeSession.id);
			window.dispatchEvent(new CustomEvent("proxima:files-changed"));
		} catch (err) {
			if (mountedRef.current && seq === actionSeq.current)
				setError(String(err));
		}
	}

	async function stopRun() {
		if (!busyRun || !activeSession) return;
		const runId = busyRun;
		const seq = ++actionSeq.current;
		try {
			await cancelRun(props.token, runId);
			if (!mountedRef.current || seq !== actionSeq.current) return;
			setBusyRun((cur) => (cur === runId ? null : cur));
			await loadMessages(activeSession.id);
			window.dispatchEvent(new CustomEvent("proxima:files-changed"));
		} catch (err) {
			if (mountedRef.current && seq === actionSeq.current)
				setError(String(err));
		}
	}

	const goalBanner =
		goal && (goal.status === "running" || goal.status === "blocked") ? (
			<div className={`goal-banner ${goal.status}`}>
				<span className="goal-dot" />
				<div className="goal-text">
					<strong>
						Goal{goal.status === "blocked" ? " · needs your input" : ""}
					</strong>
					<span>{goal.objective}</span>
				</div>
				<span className="goal-iter">
					{goal.iteration}/{goal.max}
				</span>
				{goal.status === "running" && (
					<button
						className="ghost-button"
						onClick={() => void stopGoal()}
						title="Stop the goal loop"
					>
						Stop
					</button>
				)}
			</div>
		) : null;

	const controls = (
		<div className="chat-controls">
			<span
				className={`stream-dot ${connected ? "on" : ""}`}
				title={connected ? "Stream connected" : "Stream idle"}
			/>
			{busyRun && (
				<button
					className="ghost-button icon-text chat-action"
					onClick={() => void stopRun()}
					aria-label="Cancel run"
					title="Cancel run"
				>
					<IconClose size={15} />
					<span className="chat-action-label">Cancel</span>
				</button>
			)}
			<span className="ctl-divider" />
			<label className="toolbar-control">
				<span className="ctl-icon" title="Agent">
					<IconAgents size={15} />
				</span>
				<span className="ctl-label">Agents</span>
				<Dropdown
					className="agent-dd"
					dropUp
					value={String(props.activeProfile?.id || "")}
					onChange={(id) => {
						const p = props.profiles.find(
							(profile) => profile.id === Number(id),
						);
						if (p) {
							props.onActiveProfile(p);
							const sid = activeSession?.id;
							if (sid)
								void setSessionProfile(props.token, sid, p.id).catch(
									() => undefined,
								);
						}
					}}
					options={props.profiles.map((p) => ({
						value: String(p.id),
						label: p.name,
					}))}
				/>
			</label>
			{(activeSession?.project_slug || props.activeProject?.slug) && (
				<>
					<span className="ctl-divider" />
					<button
						className="ghost-button icon-text chat-action"
						onClick={() => void startWikiDraft()}
						disabled={!activeSession || savingWiki}
						aria-label={savingWiki ? "Preparing wiki note" : "Save to wiki"}
						title="Distill this conversation into a wiki note"
					>
						<IconWiki size={15} />
						<span className="chat-action-label">
							{savingWiki ? "Preparing…" : "Save to wiki"}
						</span>
					</button>
				</>
			)}
			{/* Recipe-iteration sessions never reach this surface: the API keeps them
			    out of the session list and the recipe editor owns its own test bench.
			    So there is no "Save to recipe" arm here — only the promote path out of
			    an ordinary chat: talk until the scope is clear, then slice it into a
			    plan (the key moment of the flow). */}
			{activeSession && (props.onWorkflowDraft || props.onGraphDraft) && (
				<ConvertToWorkflowButton
					token={props.token}
					sessionId={activeSession.id}
					profileId={props.activeProfile?.id ?? null}
					engine={props.features.workflowGraph ? "graph" : "linear"}
					label="Slice into plan"
					busyLabel="Slicing into plan…"
					onDraft={draft => {
						if ("graph" in draft) props.onGraphDraft?.(draft);
						else props.onWorkflowDraft?.(draft);
					}}
					onError={setError}
				/>
			)}
		</div>
	);
	const projSlug =
		activeSession?.project_slug || props.activeProject?.slug || undefined;
	const headerProject = chatHeaderProjectLabel(
		activeSession,
		props.activeProject,
		props.projects,
	);
	return (
		<section className="chat-stage code-view">
			<header className="code-header">
				<div><p className="eyebrow">Chat</p><strong>{activeSession?.title || "New chat"}</strong></div>
				<div className="code-context"><span>{headerProject}</span><span>{props.activeProfile?.name || "No agent"}</span><button className="ghost-button icon-text code-new-session" onClick={() => void props.onNewSession()} aria-label="New chat" title="Start a new chat"><IconNewChat size={15} /><span>New chat</span></button></div>
			</header>
			{wikiNotice && (
				<div className="chat-notice" role="status">
					{wikiNotice}
				</div>
			)}
			{goalBanner}
			<ChatThread
				messages={messages}
				events={events}
				pendingRunId={busyRun}
				token={props.token}
				slug={projSlug}
				agentName={
					activeSession?.profile_name || props.activeProfile?.name || undefined
				}
				profiles={props.profiles}
				onQuickReply={submit}
				onOpenOutput={openOutput}
					onMessageUpdated={updateMessageContent}
					features={props.features}
			/>
			{error && <div className="error-bar">{error}</div>}
			<div className="chat-dock">
				{controls}
				<Composer
					disabled={!props.activeProfile}
					token={props.token}
						slug={projSlug}
						features={props.features}
					onSubmit={submit}
				/>
			</div>
			{wikiDraft && (
				<WikiNotePreview
					draft={wikiDraft}
					onCancel={() => setWikiDraft(null)}
					onSave={async (path, content, mode) => {
						if (!activeSession) return;
						const seq = ++auxActionSeq.current;
						const sessionId = activeSession.id;
						await commitWikiNote(props.token, sessionId, path, content, mode);
						if (
							!mountedRef.current ||
							seq !== auxActionSeq.current ||
							activeSessionIdRef.current !== sessionId
						)
							return;
						setWikiDraft(null);
						// In-app confirmation: desktop notify() is a no-op while this tab is focused.
						setWikiNotice(`Saved to wiki · ${path}`);
						notify("Saved to wiki", path);
					}}
				/>
			)}
		</section>
	);
}
