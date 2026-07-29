# ADR-0008: Safe update authority stays outside candidate releases

- Status: Accepted
- Date: 2026-07-29

## Context

Updating Proxima can replace executable code, restart a service, and eventually
change the database schema. The running application and a candidate release are
both inside the boundary being replaced. Neither can be trusted to decide that its
own code is safe, preserve recovery evidence, or grant itself operating-system
authority.

The first safe-update delivery group must define durable contracts without enabling
activation. It must also work across supported host families while allowing each
service manager to prove its own stop, start, sandbox, and recovery behavior later.

## Decision drivers

1. Candidate code cannot own or write promotion truth or recovery state.
2. Interrupted operations must recover from durable, replayable evidence.
3. Release identity and local provenance must bind the exact file and lockfile set.
4. Concurrent update requests must resolve to one durable active run.
5. Unsupported and unenrolled platforms must fail closed.
6. The application database remains a user-visible projection only.

## Options considered

1. Keep updating inside the running application. This is convenient, but lets the
   code being replaced mutate its own checkout, marker, database, and service.
2. Use service-manager hooks without an external state machine. This moves restart
   authority but leaves verification, interruption recovery, and cross-platform
   semantics fragmented.
3. Use one external controller contract with qualified service-manager adapters.
   This separates authority from candidates and centralizes evidence while retaining
   platform-specific proofs at a narrow seam.

## Decision

Use a root-admin enrolled external controller as the sole owner of the update lock,
append-only fsynced hash-chained journal, immutable release namespace, release
pointers, maintenance fence, backups, trust metadata, service configuration, and
recovery verdict.

The application may expose authenticated request and status projections. Its SQLite
rows do not authorize or prove promotion. Release manifests bind the exact regular
file set and the canonical Python and web lockfiles. Local provenance is unsigned
and must be reverified against the candidate tree. Both paths return an immutable
verified file set bound to the candidate commit; signed results are also bound to
the release identifier. Publication accepts that result instead of recomputing trust
from mutable source, traverses from pinned directory descriptors, copies content
into fresh controller-owned staging inodes, revalidates that tree, and atomically
renames it into the immutable namespace.

Locking and directory durability select native backends for POSIX or Windows. A
submitted nonterminal journal remains the durable single-flight owner after the
kernel lock is released. Recovery fails closed for missing, malformed, truncated,
unterminated, unreadable, substituted, or hostile-path journals.

The application consults external submission authority before updating its
projection. A stale requested or in-progress SQLite row cannot block a later
externally accepted run. The maintenance fence contains nonsecret status and is
controller-owned, application-readable, and application-nonwritable. Its read client
lives in the API package so every documented application entrypoint has the same
contract.

Maintenance activation publishes owner-bound pending state before taking an
exclusive ingress lock. Application work holds shared admission through its final
side effect. Process-backed effects use one PID-containment contract and retain
admission for the process lifetime. Cached runners are stopped when activation
becomes pending, deterministic scripts retain admission until namespace exit, and
shutdown failure keeps admission held. Recovery reconciles owner-bound pending and
active fence state with the journal before declaring a pre-fence candidate safe to
discard.

Every service-manager adapter reports unmanaged until its complete qualification
matrix passes. The foundation contains no live release switch, application-data
migration or swap, service enrollment, or activation method. Its database changes
are limited to the app-owned request/status projection schema and rows. The legacy
application and CLI apply paths refuse the operation.

## Consequences

Positive:

- A candidate cannot promote itself by changing app state.
- Kill and replay behavior has one durable source of truth.
- Service-manager differences remain isolated behind a qualification contract.
- Unsupported hosts and incomplete enrollment have an explicit safe result.

Negative:

- Installation requires an administrator-managed trust root and identity split.
- A nonterminal or unreadable journal blocks later submissions until trusted
  recovery resolves it.
- Full update activation requires target-platform adapter qualification plus
  production fault, rollback, sandbox, and soak evidence. Disposable fixture
  evidence does not enroll a service.
- Local provenance records identity metadata but are not release signatures.

## Related

- Adapter playbook: [`../adding-safe-updater-adapter.md`](../adding-safe-updater-adapter.md)
- Architecture flow: [`../reference/architecture.md`](../reference/architecture.md)
- Security boundary: [`../security-boundaries.md`](../security-boundaries.md)
- Installation contract: [`../installation.md`](../installation.md)
