# ADR-0013: Detached preview output uses OS sink helpers

- Status: Accepted
- Date: 2026-07-31

## Context

An uncontained detached child can inherit a managed command's stdout pipe and
continue writing after Stop. Closing the read end can deliver `EPIPE` or
`SIGPIPE` to that foreign-lifetime child. Keeping an asyncio reader alive avoids
that signal only until the API event loop shuts down.

Output framing also needs a byte bound. A line-count limit does not constrain a
newline-free stream.

## Decision drivers

- Never signal an uncontained detached writer through pipe closure.
- Preserve final output collected during a bounded Stop grace.
- Keep memory bounded for newline-free and post-Stop output.
- Survive API event-loop shutdown and reap resources at EOF.

## Options considered

1. Close the pipe after final-log collection. This can signal a detached writer.
2. Retain an asyncio discard task. This ties safety to the API event loop.
3. Transfer the pipe to a minimal OS helper that discards fixed-size reads and
   exits at EOF, with a small reaper independent of the event loop.

## Decision

Status-log framing retains at most a fixed byte tail for the current partial
line while continuously consuming excess output. Complete line count remains
bounded separately.

After the bounded final-log grace, Proxima transfers the still-open stdout pipe
to a minimal OS helper. The helper owns the read end independently of the API
event loop, discards fixed-size chunks without accumulation, and exits at EOF.
A daemon reaper tracks the helper only by process handle until it exits.

## Consequences

- Newline-free output cannot grow the status buffer without bound.
- Detached writers can continue after Stop and graceful API loop shutdown
  without receiving a pipe-closure signal.
- One small helper process and read descriptor remain until each inherited
  writer closes the pipe.

## Related

- Extracts the output-lifetime decision from ADR-0011.
- [ADR-0012](0012-exact-containment-proof-gates-preview-authority.md)
- `docs/security-boundaries.md`
- `docs/reference/architecture.md`
