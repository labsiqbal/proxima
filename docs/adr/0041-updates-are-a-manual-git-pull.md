# ADR-0041: Updates are a manual git pull; the safe-updater stack is removed

- Status: Accepted
- Date: 2026-08-02

## Context

ADR-0008 designed an external safe-update authority: a root-enrolled controller
owning an append-only journal, immutable release namespace, maintenance fence,
trusted probes, and qualified service-manager adapters, so that a candidate
release could never promote itself. The foundation was built (~10k lines across
`apps/safe_updater/`, `trusted-probes/safe-update/`, `infra/safe-updater/`, its
systemd unit templates, and ~4,400 lines of tests) but was never activated: the
feature flag defaulted off, no platform adapter completed qualification, no host
was ever enrolled, and every apply path failed closed.

The prune audit (#114, accepted in #117, `docs/PRUNE-SPEC.md` item A1) rated the
stack the largest piece of inert governance weight in the repo for the actual
threat model - a single owner reaching Proxima over Tailscale, with full machine
access anyway. #126 unhooked every live reference (routes, flag, maintenance
fence, ingress leases); this decision covers deleting the remains.

## Decision drivers

1. Single-owner threat model: the owner already holds SSH/sudo on the host; an
   in-app promotion authority protects no one from anyone.
2. Inert code still costs: every agent session and audit had to re-derive that
   ~10k lines were unreachable, and boundary docs had to keep describing them.
3. The update path owners actually use is `git pull` + service restart, and it
   must stay honest in docs and installer output.
4. Migration history is append-only; recorded upgrade chains must replay.

## Options considered

1. Keep the stack dormant until a future multi-tenant "secure mode" needs it -
   rejected: secure mode is explicitly out of scope, and dormant safety-critical
   code rots into a liability rather than a head start.
2. Extract it into a separate repository - rejected: nothing consumes it, and
   preservation is already served by git history and ADR-0008.
3. Delete it and state the manual update contract plainly - chosen.

## Decision

Updating Proxima is a manual, owner-performed `git pull` plus a service restart
(plus dependency sync/build for root-owned layouts, per `infra/systemd/README.md`).
The application only checks release metadata (`UpdateManager`); every in-app
apply path stays inert. The safe-updater stack is deleted: `apps/safe_updater/`,
`trusted-probes/safe-update/`, `infra/safe-updater/`, the `proxima-safe-update`
and `proxima-candidate@` unit templates, and the adapter qualification playbook.
The `self_update_runs` projection table is dropped by migration v57 while v43
stays in the append-only history. The generic browser E2E driver that lived in
the trusted-probe bundle survives at `scripts/browser-harness/` because the
`scripts/verify_*_browser.py` checks depend on it.

## Consequences

Positive:

- ~10k lines of unreachable authority code no longer need auditing, and the
  security docs describe only mechanisms that exist.
- The update contract is the one owners actually follow.

Negative:

- No staged/atomic self-update path exists; a bad pull is recovered with git,
  and schema changes rely on the migration-time database backup.
- If Proxima ever hosts untrusted tenants, an update authority would have to be
  rebuilt; ADR-0008 records the design that was proven out.

## Related

- Supersedes: [`0008-external-safe-update-authority.md`](0008-external-safe-update-authority.md)
- Prune decision trail: `docs/PRUNE-SPEC.md` (A1), issues #114/#117/#126/#127
- Update flow: [`../installation.md`](../installation.md),
  [`../reference/architecture.md`](../reference/architecture.md)
