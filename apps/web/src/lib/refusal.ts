// Rendering governance refusals (prune B5, #133).
//
// Proxima fails closed on purpose in a lot of places. The audit behind #115
// found that roughly a third of the owner's "errors everywhere" was a *correct*
// refusal that never said what to do about it - so the server now ends every
// owner-facing refusal with a concrete next step (`proxima_api/refusals.py`).
//
// These helpers are the rendering half of that contract:
//
// - `splitRefusal` separates the diagnosis from the instruction, so a screen can
//   style them apart without printing the next step twice (the API sends it both
//   inside `message` - so non-UI consumers get a complete sentence - and as its
//   own `next_step` field).
// - `refusalText` recovers the server's sentence from a thrown client error,
//   whose message is wrapped in transport noise ("… failed (400 Bad Request): ").
//   Showing `String(error)` puts "Error: Failed to write file (400 Bad
//   Request):" in front of the one sentence the owner needs.
//
// Neither helper changes a decision. They only decide how it reads.

export interface RefusalParts {
  /** What was refused and why. */
  reason: string
  /** The concrete action that clears it, or '' when the server sent none. */
  nextStep: string
}

export function splitRefusal(
  message?: string | null,
  nextStep?: string | null,
): RefusalParts {
  const full = (message || '').trim()
  const step = (nextStep || '').trim()
  if (step && full.endsWith(step)) {
    return { reason: full.slice(0, full.length - step.length).trim(), nextStep: step }
  }
  return { reason: full, nextStep: step }
}

// `api/client.ts` throws `${method} ${path} failed (${status}): ${detail}` and
// `api/files.ts` throws `${fallback} (${status} ${statusText}): ${detail}`. Both
// put the server's own sentence after the first ": " that follows a status in
// parentheses.
const TRANSPORT_PREFIX = /^.*?\((?:\d{3})(?:\s[^)]*)?\):\s*/s

export function refusalText(cause: unknown): string {
  const raw = cause instanceof Error
    ? cause.message
    : typeof cause === 'string' ? cause : ''
  const text = raw.trim()
  if (!text) return 'Something went wrong.'
  return text.replace(TRANSPORT_PREFIX, '').trim() || text
}
