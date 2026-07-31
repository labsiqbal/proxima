# ADR-0025: Preview apps use launch-specific cgroups

- Status: Accepted
- Date: 2026-07-31

## Context

Putting an app in its output supervisor's root cgroup lets service teardown
signal every process in that cgroup, including a process that no longer has
verified app authority.

## Decision drivers

- Keep the output reader alive independently of the API.
- Give each app an exact containment membership boundary.
- Never let supervisor teardown signal an escaped process.
- Stop every process that remains proven inside the managed app boundary.

## Options considered

1. Keep broker and app in one control group. Unit teardown has an overly broad
   signal boundary.
2. Rely on process groups. A child can create a new session.
3. Delegate a launch-specific child cgroup to each supervisor and limit unit
   teardown to the broker process.

## Decision

Packaged Linux supervisors create a launch-specific delegated app cgroup before
exec. The broker remains in its root service cgroup, while the app enters the
child cgroup before running owner code. Stop may signal processes still proven
inside that exact app cgroup. Broker units use process-only teardown, so a
process that escaped the app cgroup is neither trusted nor signaled. The broker
stays reconnectable until its managed app cgroup is empty.

## Consequences

- Cgroup membership strengthens process, namespace, lineage, and socket proof.
- A new broker protocol version prevents adoption across incompatible scope
  layouts.
- Hosts that cannot create the required packaged cgroup fail before app spawn.

## Related

- Supersedes ADR-0021.
- [ADR-0016](0016-live-containment-lineage-gates-preview-authority.md)
- [ADR-0024](0024-preview-generations-use-durable-launch-phases.md)
