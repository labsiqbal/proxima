# ADR-0026: Preview supervision upgrades require a drained legacy generation

- Status: Accepted
- Date: 2026-07-31

## Context

Replacing preview supervision units while an older, incompatible generation is
live can strand that app outside both the old and new manager's authority.

## Decision drivers

- Never replace supervision while legacy authority remains live.
- Avoid signaling a process merely because it resembles a preview.
- Keep production, staging, and user-service migrations fail closed.
- Refuse before changing installed units.

## Options considered

1. Restart and rely on cgroup cleanup. This can signal unverified descendants.
2. Adopt every older protocol. Incompatible ownership models cannot share proof.
3. Require operators to stop previews and verify no legacy protocol process
   remains before unit installation.

## Decision

Installers and documented system-wide updates run a same-user procfs preflight
before replacing units. Any app or broker carrying an older preview authority
protocol refuses the migration. The preflight also recognizes pre-protocol app
processes through their preview port environment plus live API lineage or
service-cgroup membership. Operators stop those previews through Proxima and
retry; no unit migration occurs while a legacy generation survives.

## Consequences

- Existing deployments have an explicit, reversible protocol migration.
- Production and staging may require both profiles to be drained when they
  share an OS user.
- Hosts without usable procfs cannot use the automated unit migration.

## Related

- [ADR-0023](0023-preview-supervisor-profiles-are-isolated.md)
- [ADR-0024](0024-preview-generations-use-durable-launch-phases.md)
