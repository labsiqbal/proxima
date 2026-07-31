import React from "react";
import type { PromptMode } from "../api/runs";
import type { ComposerAttachment } from "../components/chat/Composer";

export type WorkChatSelection = { start: number; end: number };

export type WorkChatState = {
	draft: string;
	selection: WorkChatSelection;
	mode: PromptMode;
	attachments: ComposerAttachment[];
	scrollTop: number | null;
};

type StoredWorkChatState = Record<string, WorkChatState>;

type WorkChatStateContextValue = {
	states: StoredWorkChatState;
	update: (key: string, patch: Partial<WorkChatState>) => void;
	clear: (key: string) => void;
};

const EMPTY_STATE: WorkChatState = {
	draft: "",
	selection: { start: 0, end: 0 },
	mode: "chat",
	attachments: [],
	scrollTop: null,
};

const WorkChatStateContext =
	React.createContext<WorkChatStateContextValue | null>(null);

export function workChatStateKey(
	projectSlug: string | null | undefined,
	sessionId: number | null | undefined,
): string | null {
	if (!projectSlug) return null;
	return `${projectSlug}:${sessionId ?? "new"}`;
}

function isSafeAttachment(value: unknown): value is ComposerAttachment {
	if (!value || typeof value !== "object") return false;
	const attachment = value as Partial<ComposerAttachment>;
	if (
		typeof attachment.path !== "string" ||
		typeof attachment.name !== "string" ||
		typeof attachment.img !== "boolean"
	) {
		return false;
	}
	const segments = attachment.path.replaceAll("\\", "/").split("/");
	return (
		attachment.path.length > 0 &&
		attachment.path.length <= 1000 &&
		attachment.name.length > 0 &&
		attachment.name.length <= 255 &&
		!attachment.path.startsWith("/") &&
		!/^[A-Za-z]:[\\/]/.test(attachment.path) &&
		!segments.includes("..")
	);
}

function normalizeState(value: unknown): WorkChatState | null {
	if (!value || typeof value !== "object") return null;
	const state = value as Partial<WorkChatState>;
	if (typeof state.draft !== "string" || state.draft.length > 100_000) return null;
	const selection = state.selection;
	if (
		!selection ||
		!Number.isInteger(selection.start) ||
		!Number.isInteger(selection.end) ||
		selection.start < 0 ||
		selection.end < selection.start
	) {
		return null;
	}
	if (!["chat", "brainstorm", "debate"].includes(state.mode || "")) return null;
	const attachments = Array.isArray(state.attachments)
		? state.attachments.filter(isSafeAttachment).slice(0, 20)
		: [];
	const scrollTop =
		typeof state.scrollTop === "number" &&
		Number.isFinite(state.scrollTop) &&
		state.scrollTop >= 0
			? state.scrollTop
			: null;
	return {
		draft: state.draft,
		selection: {
			start: Math.min(selection.start, state.draft.length),
			end: Math.min(selection.end, state.draft.length),
		},
		mode: state.mode as PromptMode,
		attachments,
		scrollTop,
	};
}

export function parseWorkChatState(raw: string | null): StoredWorkChatState {
	if (!raw) return {};
	try {
		const parsed = JSON.parse(raw);
		if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
		return Object.fromEntries(
			Object.entries(parsed)
				.map(([key, value]) => [key, normalizeState(value)] as const)
				.filter(
					(entry): entry is readonly [string, WorkChatState] =>
						entry[1] !== null,
				),
		);
	} catch {
		return {};
	}
}

function storageKey(ownerId: number): string {
	return `proxima.work-chat-state.v1.${ownerId}`;
}

function save(ownerId: number, states: StoredWorkChatState): void {
	try {
		localStorage.setItem(storageKey(ownerId), JSON.stringify(states));
	} catch {
		// Storage can be unavailable or full. Mounted state remains authoritative.
	}
}

export function WorkChatStateProvider({
	ownerId,
	availableKeys,
	availabilityReady = true,
	children,
}: {
	ownerId: number;
	availableKeys: string[];
	availabilityReady?: boolean;
	children: React.ReactNode;
}) {
	const [states, setStates] = React.useState<StoredWorkChatState>(() => {
		try {
			return parseWorkChatState(localStorage.getItem(storageKey(ownerId)));
		} catch {
			return {};
		}
	});
	const availableProjects = React.useMemo(
		() =>
			availableKeys
				.filter((key) => key.endsWith(":new"))
				.map((key) => key.slice(0, -":new".length)),
		[availableKeys],
	);

	React.useEffect(() => {
		if (!availabilityReady) return;
		setStates((current) => {
			const next = Object.fromEntries(
				Object.entries(current).filter(([key]) =>
					availableProjects.some((slug) => key.startsWith(`${slug}:`)),
				),
			);
			if (Object.keys(next).length === Object.keys(current).length) return current;
			save(ownerId, next);
			return next;
		});
	}, [availabilityReady, availableProjects, ownerId]);

	const update = React.useCallback(
		(key: string, patch: Partial<WorkChatState>) => {
			setStates((current) => {
				const nextState = normalizeState({
					...(current[key] || EMPTY_STATE),
					...patch,
				});
				if (!nextState) return current;
				const next = { ...current, [key]: nextState };
				save(ownerId, next);
				return next;
			});
		},
		[ownerId],
	);

	const clear = React.useCallback(
		(key: string) => {
			setStates((current) => {
				if (!(key in current)) return current;
				const next = { ...current };
				delete next[key];
				save(ownerId, next);
				return next;
			});
		},
		[ownerId],
	);

	const value = React.useMemo(
		() => ({ states, update, clear }),
		[clear, states, update],
	);
	return (
		<WorkChatStateContext.Provider value={value}>
			{children}
		</WorkChatStateContext.Provider>
	);
}

export function useWorkChatState(
	projectSlug: string | null | undefined,
	sessionId: number | null | undefined,
) {
	const context = React.useContext(WorkChatStateContext);
	if (!context) {
		throw new Error("useWorkChatState must be used within WorkChatStateProvider");
	}
	const key = workChatStateKey(projectSlug, sessionId);
	const state = key ? context.states[key] || EMPTY_STATE : EMPTY_STATE;
	const update = React.useCallback(
		(patch: Partial<WorkChatState>) => {
			if (key) context.update(key, patch);
		},
		[context, key],
	);
	const clear = React.useCallback(() => {
		if (key) context.clear(key);
	}, [context, key]);
	return { key, state, update, clear };
}
