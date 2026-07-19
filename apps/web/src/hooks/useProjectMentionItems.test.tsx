import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listReferenceFiles } from "../api/files";
import { useProjectMentionItems } from "./useProjectMentionItems";

vi.mock("../api/files", () => ({ listReferenceFiles: vi.fn() }));

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => {
		resolve = done;
	});
	return { promise, resolve };
}

describe("useProjectMentionItems", () => {
	beforeEach(() => vi.clearAllMocks());

	it("does not let a stale project response replace the active project", async () => {
		const alpha = deferred<{ files: { path: string }[]; truncated: boolean }>();
		const beta = deferred<{ files: { path: string }[]; truncated: boolean }>();
		vi.mocked(listReferenceFiles).mockImplementation((_token, slug) =>
			slug === "alpha" ? alpha.promise : beta.promise,
		);
		const { result, rerender } = renderHook(
			({ slug }) => useProjectMentionItems("token", slug),
			{ initialProps: { slug: "alpha" } },
		);
		await waitFor(() =>
			expect(listReferenceFiles).toHaveBeenCalledWith("token", "alpha"),
		);

		rerender({ slug: "beta" });
		await waitFor(() =>
			expect(listReferenceFiles).toHaveBeenCalledWith("token", "beta"),
		);
		act(() => beta.resolve({ files: [{ path: "beta.md" }], truncated: false }));
		await waitFor(() => expect(result.current).toEqual([{ path: "beta.md" }]));

		act(() => alpha.resolve({ files: [{ path: "alpha.md" }], truncated: false }));
		await act(async () => Promise.resolve());
		expect(result.current).toEqual([{ path: "beta.md" }]);
	});

	it("refreshes after project files change and keeps the last good list on an API error", async () => {
		vi.mocked(listReferenceFiles)
			.mockResolvedValueOnce({ files: [{ path: "before.md" }], truncated: false })
			.mockResolvedValueOnce({ files: [{ path: "after.md" }], truncated: false })
			.mockRejectedValueOnce(new Error("offline"));
		const { result } = renderHook(() =>
			useProjectMentionItems("token", "alpha"),
		);
		await waitFor(() => expect(result.current).toEqual([{ path: "before.md" }]));

		act(() => window.dispatchEvent(new CustomEvent("proxima:files-changed")));
		await waitFor(() => expect(result.current).toEqual([{ path: "after.md" }]));
		act(() => window.dispatchEvent(new CustomEvent("proxima:files-changed")));
		await act(async () => Promise.resolve());
		expect(result.current).toEqual([{ path: "after.md" }]);
	});
});
