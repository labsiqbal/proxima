import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePolling } from "./usePolling";

afterEach(() => {
	vi.useRealTimers();
	vi.restoreAllMocks();
});

describe("usePolling", () => {
	it("runs immediately, prevents overlap, and stops after unmount", async () => {
		vi.useFakeTimers();
		let finish: (() => void) | undefined;
		const task = vi.fn(
			() => new Promise<void>((resolve) => {
				finish = resolve;
			}),
		);
		const { unmount } = renderHook(() => usePolling(task, 100));

		expect(task).toHaveBeenCalledTimes(1);
		await act(async () => vi.advanceTimersByTime(300));
		expect(task).toHaveBeenCalledTimes(1);

		await act(async () => {
			finish?.();
			await Promise.resolve();
			vi.advanceTimersByTime(100);
		});
		expect(task).toHaveBeenCalledTimes(2);

		unmount();
		await act(async () => vi.advanceTimersByTime(500));
		expect(task).toHaveBeenCalledTimes(2);
	});

	it("does not run while disabled", () => {
		vi.useFakeTimers();
		const task = vi.fn();
		renderHook(() => usePolling(task, 100, { enabled: false }));
		vi.advanceTimersByTime(500);
		expect(task).not.toHaveBeenCalled();
	});

	it("pauses while hidden and refreshes immediately when visible", async () => {
		vi.useFakeTimers();
		const visibility = vi
			.spyOn(document, "visibilityState", "get")
			.mockReturnValue("hidden");
		const task = vi.fn();
		const { unmount } = renderHook(() => usePolling(task, 100));

		await act(async () => vi.advanceTimersByTime(500));
		expect(task).not.toHaveBeenCalled();

		visibility.mockReturnValue("visible");
		await act(async () => {
			document.dispatchEvent(new Event("visibilitychange"));
			await Promise.resolve();
		});
		expect(task).toHaveBeenCalledTimes(1);

		visibility.mockReturnValue("hidden");
		await act(async () => vi.advanceTimersByTime(500));
		expect(task).toHaveBeenCalledTimes(1);

		unmount();
		visibility.mockReturnValue("visible");
		document.dispatchEvent(new Event("visibilitychange"));
		expect(task).toHaveBeenCalledTimes(1);
	});
});
