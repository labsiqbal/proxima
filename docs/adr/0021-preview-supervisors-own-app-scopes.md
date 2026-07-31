# ADR-0021: Preview supervisors own app scopes

- Status: Accepted
- Date: 2026-07-31

## Context

An output-only broker leaves the app in the API service cgroup. A service timeout
can kill the API before it stops every app, leaving a live process without
authority. Restart adoption also needs a reconnectable process owner rather than
an asyncio child handle.

## Decision drivers

- Keep each app outside the API service lifecycle.
- Preserve output ownership and process authority across API restart.
- Signal only the exact process launched for that preview.
- Adopt only independently verified durable authority.

## Options considered

1. Stop API child groups sequentially. Service timeout still scales with app
   count.
2. Put apps in transient scopes while leaving output control in the API.
   Restart cannot recover the control channel or terminal log.
3. Let the socket-activated supervisor launch, monitor, signal, reap, and drain
   one app inside its own service cgroup.

## Decision

The preview supervisor launches the app and owns both its process handle and
output pipe. It exposes a capability-protected reconnect endpoint and durable
identity. AppManager records the supervisor PID/start time, app PID/start time,
cgroup, controller cgroup, profile, protocol, generation, and lineage. The
supervisor also accepts reconnects only from the original API service cgroup.
Restart adoption requires every saved and live value to match. Incomplete proof
remains fail closed and is never signaled.

## Consequences

- API restart and stop do not remove the app's process or output owner.
- App generations can be reconciled concurrently within a fixed service timeout.
- A stale or forged state record cannot recover signaling or proxy authority.

## Related

- Supersedes ADR-0019.
- [ADR-0020](0020-preview-lifecycles-use-project-generations.md)
- [ADR-0023](0023-preview-supervisor-profiles-are-isolated.md)
- `docs/reference/architecture.md`
