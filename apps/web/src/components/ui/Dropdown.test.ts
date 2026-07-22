import { describe, expect, it } from "vitest";
import { dropdownOptionAriaLabel } from "./Dropdown";

describe("dropdownOptionAriaLabel", () => {
	it("spaces label and badge so names do not smash together", () => {
		expect(
			dropdownOptionAriaLabel({
				label: "Auto-pick",
				badge: "Different agent",
			}),
		).toBe("Auto-pick, Different agent");
	});

	it("returns the bare label when there is no badge", () => {
		expect(dropdownOptionAriaLabel({ label: "Default" })).toBe("Default");
	});
});
