import React from "react";

/** Clamp a panel width into [low, high]. Exported for tests and callers. */
export function clampWidth(value: number, low: number, high: number): number {
	return Math.min(high, Math.max(low, value));
}

/**
 * Draggable panel width, persisted in localStorage under `key`.
 * Handles on the right edge grow right (default); set `data-grow="left"` on the
 * handle for inspectors that sit on the right of the canvas.
 */
export function useDragWidth(
	key: string,
	fallback: number,
	min: number,
	max: number,
): [number, (event: React.PointerEvent) => void] {
	const [width, setWidth] = React.useState(() => {
		const raw =
			typeof localStorage !== "undefined"
				? Number(localStorage.getItem(key))
				: NaN;
		return Number.isFinite(raw) && raw > 0
			? clampWidth(raw, min, max)
			: fallback;
	});
	React.useEffect(() => {
		try {
			localStorage.setItem(key, String(width));
		} catch {
			/* storage disabled */
		}
	}, [key, width]);
	const start = React.useCallback(
		(event: React.PointerEvent) => {
			event.preventDefault();
			const pointerId = event.pointerId;
			const startX = event.clientX;
			// Handles sit on the panel's right edge except the inspector's, which sits
			// on its left - the handle says which way growth goes.
			const direction =
				(event.currentTarget as HTMLElement).dataset.grow === "left"
					? -1
					: 1;
			let base = 0;
			setWidth((current) => {
				base = current;
				return current;
			});
			const onMove = (move: PointerEvent) => {
				if (move.pointerId !== pointerId) return;
				setWidth(
					clampWidth(
						base + direction * (move.clientX - startX),
						min,
						max,
					),
				);
			};
			const onUp = (up: PointerEvent) => {
				if (up.pointerId !== pointerId) return;
				window.removeEventListener("pointermove", onMove);
				window.removeEventListener("pointerup", onUp);
				window.removeEventListener("pointercancel", onUp);
				document.body.style.userSelect = "";
				document.body.style.cursor = "";
			};
			document.body.style.userSelect = "none";
			document.body.style.cursor = "col-resize";
			window.addEventListener("pointermove", onMove);
			window.addEventListener("pointerup", onUp);
			window.addEventListener("pointercancel", onUp);
		},
		[min, max],
	);
	return [width, start];
}
