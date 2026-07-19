import React from "react";

type PollingOptions = {
	enabled?: boolean;
	immediate?: boolean;
	restartKey?: unknown;
};

/**
 * Run one polling task at a time and always clear the interval on unmount.
 * Hidden tabs pause network work and refresh as soon as they become visible.
 * Callers keep ownership of request cancellation, errors, and state updates.
 */
export function usePolling(
	task: () => void | Promise<void>,
	intervalMs: number,
	options: PollingOptions = {},
): void {
	const { enabled = true, immediate = true, restartKey } = options;
	const taskRef = React.useRef(task);
	taskRef.current = task;

	React.useEffect(() => {
		if (!enabled) return;
		let running = false;
		const visible = () => document.visibilityState !== "hidden";
		const run = async () => {
			if (running) return;
			running = true;
			try {
				await taskRef.current();
			} finally {
				running = false;
			}
		};
		const tick = () => {
			if (visible()) void run();
		};
		const onVisibilityChange = () => {
			if (visible()) void run();
		};
		document.addEventListener("visibilitychange", onVisibilityChange);
		if (immediate) tick();
		const timer = window.setInterval(tick, intervalMs);
		return () => {
			window.clearInterval(timer);
			document.removeEventListener("visibilitychange", onVisibilityChange);
		};
	}, [enabled, immediate, intervalMs, restartKey]);
}
