/**
 * Turn a stored run-failure string into owner-facing copy.
 *
 * Hermes/ACP often persisted the whole JSON-RPC error object via str(dict):
 *   Run failed: {'code': -32603, 'message': 'Internal error', 'data': {'details': '…'}}
 * Prefer data.details (or message) so chat shows the real reason, not the dump.
 * Safe on ordinary plain-text errors — those pass through unchanged.
 */
export function formatRunError(raw: string | null | undefined): string {
	if (!raw) return "Run failed";
	let text = String(raw).trim();
	if (!text) return "Run failed";

	const hadPrefix = /^run failed:\s*/i.test(text);
	if (hadPrefix) text = text.replace(/^run failed:\s*/i, "").trim();

	const extracted = extractRpcMessage(text);
	const body = (extracted || text).trim() || "Agent run failed";
	// Keep a single Run failed: prefix for chat error bubbles.
	if (hadPrefix || extracted) return `Run failed: ${body}`;
	return body;
}

function extractRpcMessage(text: string): string | null {
	const obj = parseObject(text);
	if (!obj) return null;
	return messageFromRpc(obj);
}

function parseObject(text: string): Record<string, unknown> | null {
	const trimmed = text.trim();
	if (!trimmed || (trimmed[0] !== "{" && trimmed[0] !== "[")) return null;
	// JSON first (Codex path).
	try {
		const parsed = JSON.parse(trimmed) as unknown;
		if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
			return parsed as Record<string, unknown>;
		}
	} catch {
		/* try Python-repr next */
	}
	// Python-repr dict from str({'code': ...}) — single quotes, None/True/False.
	try {
		const jsonish = trimmed
			.replace(/\bNone\b/g, "null")
			.replace(/\bTrue\b/g, "true")
			.replace(/\bFalse\b/g, "false")
			.replace(/'/g, '"');
		const parsed = JSON.parse(jsonish) as unknown;
		if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
			return parsed as Record<string, unknown>;
		}
	} catch {
		return null;
	}
	return null;
}

function messageFromRpc(err: Record<string, unknown>): string | null {
	const data = err.data;
	let details = "";
	if (data && typeof data === "object" && !Array.isArray(data)) {
		const d = data as Record<string, unknown>;
		details = String(d.details ?? d.detail ?? "").trim();
	} else if (typeof data === "string") {
		details = data.trim();
	}
	let message = String(err.message ?? "").trim();
	// Nested JSON string inside message.
	if (message && (message[0] === "{" || message[0] === "[")) {
		try {
			const inner = JSON.parse(message) as Record<string, unknown>;
			const nested =
				inner.error && typeof inner.error === "object" && !Array.isArray(inner.error)
					? messageFromRpc(inner.error as Record<string, unknown>)
					: messageFromRpc(inner);
			if (nested) return nested;
		} catch {
			/* keep message */
		}
	}
	const generic = new Set(["internal error", "error", "server error", ""]);
	if (details) {
		if (generic.has(message.toLowerCase())) return details;
		if (!message.toLowerCase().includes(details.toLowerCase())) {
			return `${message}: ${details}`;
		}
		return message;
	}
	if (message) return message;
	if (err.code !== undefined && err.code !== null) {
		return `Agent error (code ${String(err.code)})`;
	}
	return null;
}
