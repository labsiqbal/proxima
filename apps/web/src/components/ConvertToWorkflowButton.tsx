import React from "react";
import { promoteWorkflow } from "../api/sessions";
import { useEventStream } from "../hooks/useEventStream";
import type { GraphWorkflowDraft, RunEvent, WorkflowDraft } from "../types";
import { IconWorkflows } from "./shell/icons";

// A per-session action: slice this chat into a runnable plan. Kicks off a
// run (202) and waits for the async `workflow.draft` event on the session stream,
// then hands the draft up so the app can open it in the Recipes editor (unsaved).
export function ConvertToWorkflowButton({
	token,
	sessionId,
	profileId,
	onDraft,
	onError,
	label = "Slice into plan",
	busyLabel = "Slicing into plan…",
	engine = "auto",
}: {
	token: string;
	sessionId: number;
	profileId?: number | null;
	onDraft: (draft: WorkflowDraft | GraphWorkflowDraft) => void;
	onError?: (message: string) => void;
	label?: string;
	busyLabel?: string;
	engine?: "auto" | "linear" | "graph";
}) {
	const [pending, setPending] = React.useState(false);
	// Match the draft to THIS promote's run_id — not a stale replayed draft from an
	// earlier promote on the same session (which would overwrite the recipe with old steps).
	const pendingRun = React.useRef<number | null>(null);
	const actionSeq = React.useRef(0);
	const mountedRef = React.useRef(true);

	React.useEffect(() => {
		actionSeq.current += 1;
		pendingRun.current = null;
		setPending(false);
	}, [sessionId]);

	React.useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
			actionSeq.current += 1;
			pendingRun.current = null;
		};
	}, []);

	const onEvent = React.useCallback(
		(event: RunEvent) => {
			if (!mountedRef.current) return;
			if (pendingRun.current == null || event.run_id !== pendingRun.current)
				return;
			if (event.type === "run.failed" || event.type === "run.cancelled") {
				pendingRun.current = null;
				setPending(false);
				onError?.(
					event.type === "run.failed"
						? "Plan draft failed."
						: "Plan draft cancelled.",
				);
				return;
			}
			if (event.type !== "workflow.draft") return;
			pendingRun.current = null;
			setPending(false);
			const payload = event.payload as Record<string, unknown>;
			if (typeof payload.error === "string") {
				onError?.(payload.error);
			} else onDraft(payload as unknown as WorkflowDraft);
		},
		[onDraft, onError],
	);

	useEventStream(token, pending ? sessionId : null, onEvent);

	async function start() {
		if (pending) return;
		const seq = ++actionSeq.current;
		setPending(true);
		try {
			const r = await promoteWorkflow(token, sessionId, profileId, engine);
			if (!mountedRef.current || seq !== actionSeq.current) return;
			pendingRun.current = r.run_id;
		} catch (e) {
			if (mountedRef.current && seq === actionSeq.current) {
				setPending(false);
				onError?.(String(e));
			}
		}
	}

	return (
		<button
			className="ghost-button icon-text chat-action"
			onClick={() => void start()}
			disabled={pending}
			aria-label={pending ? busyLabel : label}
			title="Slice this conversation into a plan of runnable jobs"
		>
			<IconWorkflows size={15} />
			<span className="chat-action-label">{pending ? busyLabel : label}</span>
		</button>
	);
}
