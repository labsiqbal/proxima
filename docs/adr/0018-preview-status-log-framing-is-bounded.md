# ADR-0018: Preview status log framing is bounded

- Status: Accepted
- Date: 2026-07-31

## Context

A line-count limit does not bound a stream that never emits a newline. Preview
status and stopped snapshots need a deterministic memory bound while preserving
the most recent useful output.

## Decision drivers

- Bound complete-line retention.
- Bound partial-line bytes independently.
- Keep consuming output after either retention limit is reached.
- Preserve the newest tail for status and final snapshots.

## Options considered

1. Bound only complete lines. A newline-free stream grows without limit.
2. Truncate and stop reading. Writers can block or receive pipe errors later.
3. Keep a bounded complete-line ring plus a bounded partial-line byte tail while
   continuously draining.

## Decision

Preview output retains a fixed-size ring of complete lines and a separately
bounded byte tail for the current partial line. Excess bytes and old lines are
dropped while reading continues.

## Consequences

- Newline-free output has a fixed memory ceiling.
- Status and final snapshots contain the newest bounded tail.
- Framing is independent of API polling frequency.

## Related

- Supersedes ADR-0013.
- [ADR-0019](0019-launch-time-broker-owns-preview-output.md)
- `docs/reference/architecture.md`
