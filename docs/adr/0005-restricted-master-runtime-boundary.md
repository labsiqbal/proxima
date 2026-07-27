# ADR-0005: Restricted Master runtime boundary

- Status: Accepted
- Date: 2026-07-27

## Context

Master must chat and delegate through Proxima product tools without inheriting the
native authority of its backing runner. Prompt instructions and a read-only cwd do
not remove runner-native tools, provider credentials, environment paths, or schema
drift at the final model request.

## Decision drivers

- exact server ownership of the model-visible authority surface
- fail-closed behavior before a turn when the installed adapter drifts
- no provider bearer, host path, runtime path, or runner home in model context
- bounded and unambiguous loopback transport
- typed owner-scoped product actions with durable replay protection

## Options considered

1. Prompt-only restrictions - portable, but not an enforceable authority boundary.
2. Trust app-server configuration alone - simpler, but native schemas can still be
   constructed internally.
3. Use a restricted adapter plus a private provider firewall and typed broker -
   more boundaries to verify, but each authority transfer becomes explicit.

## Decision

Only adapters with real hostile conformance evidence may set
`RunnerSpec.master_chat_only`. Codex is the sole production adapter in this release.
It runs with a dedicated managed home, empty capability roots, read-only empty
scratch, no ordinary runner environment inheritance, and rejection of every native
permission or native tool event.

Before a turn, Proxima verifies the Codex version, completes the strict app-server
handshake, and registers the exact `MasterToolBroker` schemas on an ephemeral thread.
A private IPv4-loopback firewall exposes one secret Responses route. It discards
runner-generated developer context and tools, reconstructs only the attested
server-owned schemas, rejects drift and ambiguous transport, and buffers a bounded
identity-encoded provider response before releasing any bytes.

Product actions execute only through closed broker schemas inside Proxima. The
broker resolves ownership and Container/Area IDs, uses `TaskDelegationService`, and
records root-turn envelope hashes for replay protection. Task execution policy and
repo landing review remain separate.

## Consequences

- Other runners remain unselectable for Master until equivalent hostile evidence
  and an enforceable adapter exist.
- A behaviorally incompatible Codex build fails before model input is sent.
- The Codex HTTP fallback may omit its dynamic carrier only after exact schemas were
  attested on the same process and thread setup.
- Provider streaming is buffered at this boundary to prevent partial trusted
  responses.
- Master remains a single-owner orchestration feature, not a multi-tenant sandbox.

## Related

- [Runner conformance](../runner-conformance.md)
- [Prompt-injection hardening](../prompt-injection-hardening.md)
- [Security boundaries](../security-boundaries.md)
- [ADR-0004](0004-durable-task-delegation-boundary.md)
