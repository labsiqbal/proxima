import { describe, expect, it } from "vitest";
import { reviewTitleAriaLabel } from "./MessageReviewSidecar";

describe("reviewTitleAriaLabel", () => {
	it("keeps Validate spaced from the summary and names expand state", () => {
		expect(
			reviewTitleAriaLabel("Pick a reviewer for a sidecar review", true),
		).toBe("Validate, Pick a reviewer for a sidecar review. Collapse");

		expect(
			reviewTitleAriaLabel("Pi · needs_work · 5 gaps · revised available", false),
		).toBe("Validate, Pi · needs_work · 5 gaps · revised available. Expand");
	});

	it("falls back when summary is empty", () => {
		expect(reviewTitleAriaLabel("", false)).toBe(
			"Validate, sidecar review. Expand",
		);
	});
});
