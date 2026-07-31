# ADR-0011: Preview containment membership and detached output

- Status: Superseded by ADR-0012
- Date: 2026-07-31

## Context

ADR-0010 established connected-socket ownership checks, but a manager-wide
containment flag cannot prove that a particular listener belongs to a particular
launch. A managed command can disclose its environment marker to another
same-user process outside its PID namespace. Treating that marker as containment
proof would authorize the outside listener.

Bounded final-log capture creates a second lifetime boundary. An uncontained
detached child may inherit the managed command's stdout pipe after Stop returns.
Closing Proxima's read end would deliver `EPIPE` or `SIGPIPE` to later child
writes, which violates the rule that Stop never signals an uncontained child.

The automatic dual-interface relay and authentication-before-resolution ordering
also refine the original preview transport decision.

## Decision drivers

- Grant preview authority only from exact, launch-specific ownership evidence.
- Preserve safe reparented-lineage classification without making a marker an
  authority token.
- Return Stop promptly with every log byte collected during its bounded grace.
- Keep detached stdout writers alive without retaining unbounded output or app
  state.
- Preserve local and tailnet reachability without wildcard relay binding.
- Authenticate preview capability before procfs ownership work.

## Options considered

1. Trust the launch marker when containment is enabled. This cannot prove which
   PID namespace owns the marked process.
2. Trust current ancestry alone. This loses reparented descendants and cannot
   prove that containment owns their remaining lifetime.
3. Record Bubblewrap's launch-specific PID namespace identity and require socket
   owners to match it as well as the lineage marker. This supplies independent,
   exact membership evidence.
4. Close inherited stdout after the final-log grace. This bounds resources but
   indirectly signals detached writers.
5. Transfer stdout to a background discard reader. This keeps the pipe valid,
   bounds memory, and lets Stop return independently of detached lifetime.

## Decision

Each contained app launch records Bubblewrap's PID namespace identity from its
information descriptor. For a contained launch, procfs must map every socket
owner to that exact namespace and every owner must carry the launch lineage
marker, including owners that retain the managed process group. An uncontained
launch still requires exact membership in the launch process group. A marker
without exact containment membership, missing namespace evidence, or unreadable
membership fails closed as `ownership_unknown`. The same rule applies at
readiness and at the connected upstream socket before protocol bytes are sent.

After the bounded final-log grace, Stop cancels only the log-collecting reader,
flushes its available partial line into the stopped snapshot, and transfers the
still-open stdout stream to a background discard reader. The discard reader
accumulates no output and retains no app log or lifecycle state. The manager
tracks it only until EOF, then removes the completed task. Stop never closes that
read end or signals the detached writer.

Under automatic binding, one relay port binds separately on loopback and the
Tailscale interface when present. Capability authentication completes before
target resolution or procfs scanning on relay and subdomain paths.

## Consequences

- A same-user helper cannot gain preview authority by copying a launch marker.
- Detached contained listeners depend on Bubblewrap information and procfs
  namespace evidence. Missing evidence disables preview rather than weakening
  the boundary.
- One lightweight discard task and pipe read end remain for each living detached
  writer until that writer closes stdout.
- Output written after Stop is discarded and cannot mutate the bounded stopped
  log snapshot.
- Local and tailnet browser origins reach the same capability-gated relay without
  wildcard exposure.

## Related

- Supersedes ADR-0010.
- `docs/security-boundaries.md`
- `docs/reference/architecture.md`
- `docs/CAPABILITIES.md`
