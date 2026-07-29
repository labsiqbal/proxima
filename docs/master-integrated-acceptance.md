# Master orchestrator integrated acceptance

This is the release evidence matrix for the accepted Master orchestrator vision.
It is anchored to the authoritative [ADR index](adr/README.md), which contains
exactly nine accepted records. It also records the six ordered build gates,
groups 14-16 safe-update boundaries, security invariants, APIs, migrations, UI,
runner conformance, graph isolation, and fault cases. This remains a
pre-activation record: `feature_master_orchestrator` and
`feature_safe_self_update` both default off.

## Accepted ADR matrix

| Accepted record | Decision covered | Direct implementation and test evidence |
| --- | --- | --- |
| [ADR-0001](adr/0001-workflow-execution-model.md) | Proxima owns durable orchestration primitives while runners own intelligence. | `graph.py`, `graph_executor.py`, `graph_advancers.py`, and `run_prompting.py` keep graph state, scheduling, review, and execution seams server-owned. `test_graph.py`, `test_graph_executor.py`, and `test_run_prompting.py` exercise those seams. |
| [ADR-0002](adr/0002-license-agpl.md) | The project remains AGPL-3.0-or-later with commons governance. | `LICENSE`, `CONTRIBUTING.md`, and the in-app source link retain the accepted licensing boundary. This acceptance run's explicit no-DCO delivery exception does not rewrite, amend, re-sign, or drop existing commits. |
| [ADR-0003](adr/0003-evolutionary-architecture.md) | Volatile runner behavior stays behind stable registries and fitness gates, with documentation shipped alongside code. | `runner_specs.py`, `runners.py`, the generated-doc drift check, this matrix, and the focused conformance tests form the stable seam and release fitness record. |
| [ADR-0004](adr/0004-durable-task-delegation-boundary.md) | Task creation and start are one server-owned durable boundary. | `TaskDelegationService`, `worktrees.py`, and `run_advancers.py` own provenance, dependency, one-Area, claim, and landing policy. `test_task_delegation.py`, `test_worktrees.py`, and `test_master_runtime_security.py` cover repo review, Ops in-place execution, idempotency, and rejection paths. |
| [ADR-0005](adr/0005-restricted-master-runtime-boundary.md) | Master receives only bounded Proxima product tools through a chat-only runner boundary. | `master_runner_conformance`, `codex_master_proxy.py`, `MasterToolBroker`, empty capability roots, read-only scratch, and permission denial form the boundary. `test_master_runtime_conformance.py`, `test_master_runtime_security.py`, and `test_runner_specs.py` cover hostile shell, file, browser, skill, MCP, schema, transport, and version attempts. |
| [ADR-0006](adr/0006-master-context-is-layered-and-scoped.md) | Fleet, Live, Knowledge, and Code context are typed, scoped, provenance-tagged, and fail closed. | `graph_context.py`, `context_router.py`, `code_graph_lifecycle.py`, and `knowledge_graph_lifecycle.py` resolve registered Container and Area ids rather than model paths. Their focused tests cover scope leakage, last-good retention, path refusal, freshness, and Live-state independence. |
| [ADR-0007](adr/0007-master-focus-is-a-durable-execution-boundary.md) | Focus is a durable execution boundary on one roving thread, never copied sessions. | Migrations 38-42, `master_focus.py`, immutable message and run attribution, and per-turn runner recycle preserve epochs and history. `test_master.py`, `test_master_persistence.py`, and `test_master_supervision_projection.py` cover pending Focus, restart, delayed projection, deletion, and exact Roving, Fleet, and Container views. |
| [ADR-0008](adr/0008-external-safe-update-authority.md) | Candidate releases never own update authority or production switching. | `apps/safe_updater/` keeps policy, journal, locks, fixture assembly, probes, fencing, and switching in the external controller model. Candidate, switch, recovery, and process-containment tests use disposable roots, fake services, clone data, and no signing key or privileged updater. |
| [ADR-0009](adr/0009-one-durable-master-interface-state.md) | Home and popup consume one durable provider, and runner selection uses dynamic server eligibility. | `MasterStateProvider`, `MasterScreen`, `MasterPopup`, shared conversation and composer components, and `/api/runners/detect` implement one state owner and one eligibility source. Focused frontend tests plus the replayable browser scenario below cover state sharing, selector refusal, modal semantics, labeled controls, and the Home bridge. |

## Six ordered build gates

Each gate depends on the previous gates. A later pass does not excuse a failure in
an earlier boundary.

| Gate | Required result | Direct evidence |
| --- | --- | --- |
| 1. Owned orchestration and Task substrate | Durable workflow state and one Task delegation boundary exist before Master can delegate. | ADR-0001 and ADR-0004; graph, delegation, dependency, worktree, and landing tests. |
| 2. Native Master identity and restricted runtime | Alpha migrates in place, Master is product-native, and an unsupported runner cannot create a turn. | Migrations 31 and 33-42; `master_persistence.py`; ADR-0005; persistence, runtime security, and conformance tests. |
| 3. Scoped context and graph lifecycle | Context uses registered identities, keeps graphs isolated, retains last-good generations, and never makes Live state depend on graph health. | ADR-0006; graph adapter, Code lifecycle, Knowledge lifecycle, and context-router tests. |
| 4. Durable Focus and history | Focus transitions, prompts, delayed output, and history projections retain immutable scope across restart and deletion. | ADR-0007; migrations 38-42; Focus, persistence, and projection tests. |
| 5. Shared supervision and interface | Satpam remains recovery owner while one frontend provider serves Home and popup through one durable SSE stream. | `master_supervisor.py`, `master_projection.py`, ADR-0009, provider and component tests, and the browser replay below. |
| 6. External candidate and fault gates | Build, clone migration, candidate probe, and switch or rollback faults are proven only in disposable controller fixtures. | ADR-0008; groups 14-16 candidate, controller, fence, journal, rollback, and process-containment tests. |

## Integrated invariant matrix

| Requirement | Direct evidence |
| --- | --- |
| Master and safe self-update remain disabled until their gates pass | `DEFAULT_CONFIG`, `scripts/smoke-fresh`, and `test_feature_flags.py` assert both defaults false and feature-off routes inert before side effects. |
| Alpha-to-Master migration preserves identity and data | Migrations 31 and 33-42 plus `master_persistence.py` preserve primary keys and compatibility aliases. Persistence and migration tests cover fresh, upgrade, conflict, rerun, and historical attribution cases. |
| Unsupported or unavailable runners are not selectable | `/api/runners/detect` publishes static `masterChatOnly` plus dynamic `masterEligible` and `masterUnavailableReason`, all derived from `master_runner_conformance` on the server runtime path. The UI enables only `masterEligible=true`; settings, message creation, and worker spawn repeat conformance. |
| Master supervision does not duplicate Satpam authority | `MasterSupervisor` claims eligible queued Tasks only. Satpam remains the sole detector and recovery owner. Projection tests cover restart reconciliation, capacity, attribution, and idempotency. |
| UI and accessibility use one state owner | `MasterStateProvider` owns the canonical session, cursor, EventSource, draft, target, Focus, toast queue, and scroll state. Home and popup share consumers; modal focus, Escape, focus return, keyboard controls, and live-region priority have focused tests. |
| Graph and Live state remain isolated | Code and Knowledge graph state are per Area or Container, atomic, provenance-tagged, local-only by default, and last-good preserving. Live status reads SQLite independently. |
| Candidate proof cannot touch production | Candidate build, migrated clone, fixture database, workspace, runner home, and browser profile live under disposable roots. A policy-pinned, read-only, version-only fake Codex proves selector eligibility but cannot start a turn. Bubblewrap and path validation exclude live releases, data, services, pointers, fences, backups, journals, and signing authority. |
| Fault recovery is fixture-proven | Controller tests cover journal replay, phase interruption, WAL and SHM quarantine, writable proofs, process drain, both-pointer rollback, breaker behavior, asset identity, and adapter refusal. |
| API and schema references remain synchronized | `scripts/gen_docs.py` preserves an existing generated footer when the semantic body is unchanged. `test_gen_docs.py` covers repeatability, and the mandatory end-of-work generation detects drift. |

## Reproducible browser evidence

The tracked `master-popup-home` scenario in
[`trusted-probes/safe-update/browser-scenarios.json`](../trusted-probes/safe-update/browser-scenarios.json)
is executed by the tracked Chrome DevTools driver
[`browser.py`](../trusted-probes/safe-update/browser.py). It asserts:

- a labeled popup trigger with dialog semantics
- one modal dialog with labeled composer, target, Home, and close controls
- the popup-to-Home bridge and labeled Focus, history, runner, and work-panel controls
- an enabled Codex option only when server-published dynamic eligibility passes
- the absence of any enabled option lacking `data-master-eligible=true`

Replay it from the repository root:

```bash
mkdir -p /tmp/no-mistakes-evidence
apps/api/.venv/bin/python scripts/verify_master_browser.py \
  > /tmp/no-mistakes-evidence/master-browser.json
```

The replay builds the current web bundle, creates a temporary database, workspace,
runner home, browser profile, loopback server, authenticated owner, Container, and
fake Codex 0.145.0 binary, runs the tracked real-browser assertions, prints the
reviewable transcript, and removes the fixture. It explicitly keeps
`feature_safe_self_update` off. It does not enroll an updater, activate
production, switch a live release, replace live data, control a real service, or
use signing authority.

The same scenario is part of the separately installed, policy-pinned candidate
probe suite. Candidate evidence records the scenario transcript digest alongside
the frozen release identity and complete asset digest. The source-controlled
scenario and replay command, rather than a standalone screenshot hash, define the
reviewable UI acceptance contract.

## Activation decision

This matrix proves implementation and disposable-fixture coverage. It does not
activate either feature. Master remains off until an operator explicitly accepts
the runner and product gates for that installation. Safe self-update remains off
until a qualified external updater is separately installed and enrolled.
