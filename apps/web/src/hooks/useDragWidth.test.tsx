import { act, renderHook } from "@testing-library/react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clampWidth, useDragWidth } from "./useDragWidth";

afterEach(() => {
	vi.restoreAllMocks();
	localStorage.clear();
});

describe("clampWidth", () => {
	it("clamps into [low, high]", () => {
		expect(clampWidth(100, 240, 520)).toBe(240);
		expect(clampWidth(300, 240, 520)).toBe(300);
		expect(clampWidth(900, 240, 520)).toBe(520);
	});
});

describe("useDragWidth", () => {
	beforeEach(() => {
		localStorage.clear();
	});

	it("uses fallback when storage is empty", () => {
		const { result } = renderHook(() =>
			useDragWidth("proxima.test.width", 280, 240, 520),
		);
		expect(result.current[0]).toBe(280);
	});

	it("reads and clamps a stored width", () => {
		localStorage.setItem("proxima.test.width", "999");
		const { result } = renderHook(() =>
			useDragWidth("proxima.test.width", 280, 240, 520),
		);
		expect(result.current[0]).toBe(520);
	});

	it("persists width to localStorage", () => {
		const { result } = renderHook(() =>
			useDragWidth("proxima.test.width", 280, 240, 520),
		);
		expect(localStorage.getItem("proxima.test.width")).toBe("280");

		const handle = document.createElement("div");
		const start = result.current[1];
		act(() => {
			start({
				preventDefault: () => undefined,
				pointerId: 1,
				clientX: 100,
				currentTarget: handle,
			} as unknown as ReactPointerEvent);
		});
		act(() => {
			window.dispatchEvent(
				new PointerEvent("pointermove", { pointerId: 1, clientX: 160 }),
			);
		});
		expect(result.current[0]).toBe(340);
		expect(localStorage.getItem("proxima.test.width")).toBe("340");
	});

	it("grows left when data-grow=left", () => {
		const { result } = renderHook(() =>
			useDragWidth("proxima.test.inspector", 280, 240, 520),
		);
		const handle = document.createElement("div");
		handle.dataset.grow = "left";
		const start = result.current[1];
		act(() => {
			start({
				preventDefault: () => undefined,
				pointerId: 2,
				clientX: 400,
				currentTarget: handle,
			} as unknown as ReactPointerEvent);
		});
		// Drag pointer left (negative delta) widens a right-side panel.
		act(() => {
			window.dispatchEvent(
				new PointerEvent("pointermove", { pointerId: 2, clientX: 340 }),
			);
		});
		expect(result.current[0]).toBe(340);
	});

	it("ignores non-positive stored values", () => {
		localStorage.setItem("proxima.test.width", "0");
		const { result } = renderHook(() =>
			useDragWidth("proxima.test.width", 280, 240, 520),
		);
		expect(result.current[0]).toBe(280);
	});
});
