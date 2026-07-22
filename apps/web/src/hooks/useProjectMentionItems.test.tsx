import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listArtifacts, listReferenceFiles } from "../api/files";
import {
	mergeProjectMentionItems,
	useProjectMentionItems,
} from "./useProjectMentionItems";

vi.mock("../api/files", () => ({
	listReferenceFiles: vi.fn(),
	listArtifacts: vi.fn(),
}));

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => {
		resolve = done;
	});
	return { promise, resolve };
}

describe("mergeProjectMentionItems", () => {
	it("lists typed artifacts ahead of plain files and dedupes by path", () => {
		expect(
			mergeProjectMentionItems(
				[
					{ path: "src/app.tsx" },
					{ path: "artifacts/media/images/hero.png" },
					{ path: "notes/brief.md" },
				],
				[
					{
						path: "artifacts/media/images/hero.png",
						title: "hero.png",
						type: "image",
					},
					{
						path: "artifacts/design/launch",
						title: "Launch post",
						type: "design",
					},
				],
			),
		).toEqual([
			{
				path: "artifacts/media/images/hero.png",
				title: "hero.png",
				type: "image",
			},
			{
				path: "artifacts/design/launch",
				title: "Launch post",
				type: "design",
			},
			{ path: "src/app.tsx" },
			{ path: "notes/brief.md" },
		]);
	});
});

describe("useProjectMentionItems", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [] });
	});

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

	it("merges produced artifacts into the mention set with kind metadata", async () => {
		vi.mocked(listReferenceFiles).mockResolvedValue({
			files: [{ path: "src/app.tsx" }, { path: "artifacts/media/images/hero.png" }],
			truncated: false,
		});
		vi.mocked(listArtifacts).mockResolvedValue({
			artifacts: [
				{
					path: "artifacts/media/images/hero.png",
					title: "hero.png",
					type: "image",
				},
				{
					path: "artifacts/design/launch",
					title: "Launch post",
					type: "design",
				},
			],
		});
		const { result } = renderHook(() =>
			useProjectMentionItems("token", "alpha"),
		);
		await waitFor(() =>
			expect(result.current).toEqual([
				{
					path: "artifacts/media/images/hero.png",
					title: "hero.png",
					type: "image",
				},
				{
					path: "artifacts/design/launch",
					title: "Launch post",
					type: "design",
				},
				{ path: "src/app.tsx" },
			]),
		);
		expect(listArtifacts).toHaveBeenCalledWith(
			"token",
			"alpha",
			60 * 24 * 365,
		);
	});

	it("keeps files when the artifact scan fails and refreshes after file changes", async () => {
		vi.mocked(listReferenceFiles)
			.mockResolvedValueOnce({ files: [{ path: "before.md" }], truncated: false })
			.mockResolvedValueOnce({ files: [{ path: "after.md" }], truncated: false })
			.mockRejectedValueOnce(new Error("offline"));
		vi.mocked(listArtifacts)
			.mockRejectedValueOnce(new Error("artifacts down"))
			.mockResolvedValueOnce({ artifacts: [] })
			.mockResolvedValueOnce({ artifacts: [] });
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
