# ADR-0039: Master orchestrator ships enabled

- Status: Accepted
- Date: 2026-08-01
- Note (2026-08-02): the feature-flag system (including the
  `PROXIMA_FEATURE_MASTER_ORCHESTRATOR` off switch this ADR retained) was
  removed entirely in prune A2 (#129); Master is now unconditional.

## Context

Master is the delegation half of the product: the Delegate surface, the hidden
orchestrator identity, the schema-validated tool broker, worker slots, the active
queue, and job checkpoints. It has shipped behind the server-owned
`feature_master_orchestrator` flag, defaulting off, since the integrated slices
landed.

That default was a staging decision, not a product one. It existed while the
integrated acceptance matrix and the Linux daily-driver matrix were still being
assembled, and it said so: Master would stay off "until an operator explicitly
accepts the runner and product gates for that installation". Both matrices now
pass, and the daily-driver acceptance fixtures already run with Master enabled -
the qualified configuration is the enabled one.

Leaving the flag off past that point costs more than it protects. A default-off
install presents a product that describes delegation on its own home screen and
then omits every entry point to it; the surface is not discoverable as
configuration, so it reads as a missing or broken feature rather than a choice.
Meanwhile the gate is a blunt instrument: it is not what keeps an install safe.

## Decision drivers

1. A shipped default should describe the product, not the state of its rollout.
2. The qualified, tested configuration should be the one owners receive.
3. Enabling a surface must stay distinct from enabling autonomy.
4. Owners who want a plain chat and workflow cockpit need that to remain reachable.
5. The flag must keep working as a complete off switch, not become vestigial.

## Options considered

1. Keep the flag off and document how to turn it on. Honest, but every install
   starts with a product whose headline capability is invisible, and the
   documentation gap is precisely what makes it look broken.
2. Remove the flag and always run Master. Simplest code, but it deletes the
   supported way to run Proxima without a delegation surface, and it removes the
   containment switch the security and prompt-injection docs rely on.
3. Default the flag on and keep it fully honoured when set off.

## Decision

`feature_master_orchestrator` defaults to on. `DEFAULT_CONFIG`, the env parsing in
`apps/api/scripts/serve.py` and `apps/api/proxima_api/main.py`, the config written
by `scripts/proxima init-config`, and `.env.example` all express that default.

Setting `PROXIMA_FEATURE_MASTER_ORCHESTRATOR=0` remains a complete off switch with
its existing semantics, unchanged by this ADR: canonical and deprecated Master
routes reject use, the supervisor never starts, the run worker leaves Master turns
and Master-owned Task runs queued, feature-off startup provisions no Master runner
home, and the web shell omits Delegate navigation and deep links. The safe-updater
sandbox continues to pin the flag off explicitly.

Three protections carry the safety weight this default no longer does, and none of
them are relaxed here:

- **Autonomy stays opt-in.** `MasterSupervisor` starting is not the same as
  unattended work starting. Budgeted unattended starts remain a separate per-owner
  toggle, off until set.
- **Runners fail closed.** Delegation needs an authenticated Master-eligible
  adapter. Without one the surface is present and every dispatch refuses; the
  runner conformance contract is unchanged.
- **The network boundary is unchanged.** Proxima remains single-user behind a
  loopback, Tailscale, or Access boundary plus an owner password.

`feature_safe_self_update` is unaffected and stays off until a qualified external
updater is separately installed and enrolled (ADR-0008).

## Consequences

Positive:

- The default install matches the product the docs and UI describe.
- Owners receive the configuration the acceptance matrices actually qualify.
- The gap between "feature gated during rollout" and "feature missing" closes.

Negative:

- New installs start `MasterProjectionService` and `MasterSupervisor` at boot, so
  a default install carries their startup cost and log surface whether or not the
  owner delegates anything.
- Upgrading installs that relied on the default rather than an explicit `0` gain
  the Delegate surface without acting. The flag is the documented remedy, but the
  change is visible.
- Feature-off remains a supported path that has to stay tested, now as the
  non-default branch rather than the default one.

## Related

- Integrated acceptance: [`../master-integrated-acceptance.md`](../master-integrated-acceptance.md)
- Linux daily-driver support: [`0028-linux-first-daily-driver-support.md`](0028-linux-first-daily-driver-support.md)
- Restricted Master runtime: [`0005-restricted-master-runtime-boundary.md`](0005-restricted-master-runtime-boundary.md)
- Durable delegation boundary: [`0004-durable-task-delegation-boundary.md`](0004-durable-task-delegation-boundary.md)
- Safe update authority: [`0008-external-safe-update-authority.md`](0008-external-safe-update-authority.md)
- Security boundaries: [`../security-boundaries.md`](../security-boundaries.md)
