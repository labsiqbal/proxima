# ADR-0004: Durable Task delegation is one server-owned boundary

- Status: Accepted
- Date: 2026-07-27

## Context

Work, Home quick Tasks, Alpha, Recipes, and the future Master all create the same
underlying sessions, jobs, runs, worktrees, and review states. Implementing creation
and start separately in every caller made timeout recovery, exact Area binding, and
cross-Area dependency behavior inconsistent.

A Task may start after its request transaction commits, and a process can stop at any
point in that gap. A repeated model tool envelope can also replay the same mutation.
Those cases must not create duplicate jobs or leave a graph Task running without a
node run.

## Decision drivers

1. One Task has exactly one Container and one active Area.
2. Creation is atomic and safely repeatable after a timeout.
3. Start intent survives a process restart.
4. Cross-Area work is several visible Tasks with durable dependency edges.
5. Execution permission policy does not weaken landing policy.
6. Existing Work, Alpha, Recipe, worktree, and physical Ops behavior remains
   compatible.

## Options considered

1. Keep caller-owned job creation and share helper fragments. This preserves local
   control but leaves transaction and recovery behavior dependent on each caller.
2. Make Tasks an in-memory Master concept and project them into jobs later. This adds
   a second lifecycle ledger and loses restart safety.
3. Use one server-owned delegation service while keeping `jobs` as lifecycle truth.
   This centralizes authority without replacing proven execution machinery.

## Decision

Use `TaskDelegationService` as the creation, idempotency, dependency, and retryable
start boundary for scoped Tasks.

The service validates owner, Container, Area, Task-agent, origin, and Recipe
identities. It commits the worker session, job, delegation audit, dependency edges,
and durable start intent in one transaction before starting execution. An
idempotency identity belongs to one owner and one request fingerprint. Full replay
returns the existing Task before revalidating mutable referenced resources.

Dependency edges form a database-enforced DAG. A prerequisite cannot be deleted while
a dependent exists. A dependent starts only after every required status is reached,
and failed prerequisites remain visible as durable blockers.

Repo Tasks use the existing external worktree and always stop for diff review before
local merge. Delegated Ops Tasks run in physical `ops/` and finish without a landing
review. Explicit Recipe gates remain in-run decisions and are not inferred from
landing policy.

## Consequences

Positive:

- Every scoped caller gets the same transaction and restart guarantees.
- Cross-Area decomposition remains visible and auditable.
- Duplicate tool envelopes and HTTP retries can return the original Task.
- Existing jobs, runs, worktrees, checkpoints, and Satpam remain lifecycle truth.

Negative:

- Status transitions that satisfy or fail prerequisites must notify the service.
- Deleting a prerequisite requires deleting its dependents first.
- Graph start recovery needs an explicit reconciliation path for a committed
  `running` claim with no dispatched node run.
- This boundary constrains authority and consistency, but it is not an OS sandbox.

## Related

- Feature and extension contract: [`../task-delegation.md`](../task-delegation.md)
- Architecture flow: [`../reference/architecture.md`](../reference/architecture.md)
- Security boundary: [`../security-boundaries.md`](../security-boundaries.md)
- Existing execution model: [ADR-0001](0001-workflow-execution-model.md)
