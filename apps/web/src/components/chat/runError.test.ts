import { describe, expect, it } from "vitest";
import { formatRunError } from "./runError";

describe("formatRunError", () => {
	it("extracts Hermes data.details from a Python-repr dump with Run failed prefix", () => {
		const raw =
			"Run failed: {'code': -32603, 'message': 'Internal error', 'data': {'details': 'No LLM provider configured. Run `hermes model` to select a provider, or run `hermes setup` for first-time configuration.'}}";
		expect(formatRunError(raw)).toBe(
			"Run failed: No LLM provider configured. Run `hermes model` to select a provider, or run `hermes setup` for first-time configuration.",
		);
	});

	it("extracts details from JSON-RPC JSON dumps", () => {
		const raw = JSON.stringify({
			code: -32603,
			message: "Internal error",
			data: { details: "rate limited by provider" },
		});
		expect(formatRunError(raw)).toBe("Run failed: rate limited by provider");
	});

	it("keeps ordinary plain-text errors intact", () => {
		expect(formatRunError("Run failed: Hermes runner timed out")).toBe(
			"Run failed: Hermes runner timed out",
		);
		expect(formatRunError("something broke")).toBe("something broke");
	});

	it("joins non-generic message with details", () => {
		const raw = JSON.stringify({
			message: "Provider error",
			data: { details: "quota exceeded" },
		});
		expect(formatRunError(`Run failed: ${raw}`)).toBe(
			"Run failed: Provider error: quota exceeded",
		);
	});

	it("handles empty input", () => {
		expect(formatRunError("")).toBe("Run failed");
		expect(formatRunError(null)).toBe("Run failed");
	});
});
