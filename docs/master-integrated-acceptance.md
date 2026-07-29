# Master orchestrator integrated acceptance

This is the release evidence matrix for the accepted Master orchestrator vision.
It covers the nine accepted ADRs, the six-slice build order, the groups 14-16
safe-self-update plan, and the final integrated hardening pass. It is a
pre-activation record: neither `feature_master_orchestrator` nor
`feature_safe_self_update` is enabled by this document.

Self-update evidence in this matrix is fixture-only, test-service-only, and
candidate-data-only. It does not authorize privileged updater enrollment,
production service activation, live data or release changes, or signing-key use.

## Requirement matrix

| Requirement / boundary | Direct current-state evidence |
| --- | --- |
| Native Proxima Master, with firstmate external rather than embedded (ADRs 1-2) | `master_runtime.py`, `master_supervisor.py`, and `routes/master.py` own the runtime. No firstmate package is imported by the application. `test_master.py` and `test_master_supervision_projection.py` exercise the native durable identity and supervision path. |
| Master only chats, delegates, and tracks durable one-Area Tasks (ADR 3) | `MasterToolBroker` exposes bounded typed product tools; `TaskDelegationService` owns creation and start. `test_master_runtime_security.py` and `test_task_delegation.py` cover schema validation, provenance, idempotency, and fail-closed Task ownership. |
| Generic Container with one physical Ops Area, byte preservation, and path isolation (ADR 5) | `project_areas.py`, `container_registry.py`, and `container_ops_migrations` preserve the persistence bridge while exposing Container contracts. `test_container_registry.py`, `test_containers_api.py`, and `test_project_areas.py` cover collision refusal, compatibility roots, symlink containment, and registry projection. |
| Repo Tasks isolate, review, and local-merge; Ops Tasks run in place without review (ADR 4) | `worktrees.py`, `task_delegation.py`, and `run_advancers.py` derive landing policy from the selected Area. `test_worktrees.py`, `test_master_runtime_security.py`, and `test_task_delegation.py` cover worktree, review, Ops in-place, and one-Area rejection paths. |
| Dependencies are visible, cycle-safe, and block downstream Tasks honestly | `task_dependencies` triggers and `TaskDelegationService` use transactional graph checks. `test_task_delegation.py` covers atomic batch creation, blocked reasons, dependency readiness, and cycle rejection. |
| Alpha-to-Master migration is in place, identity-preserving, and feature-off safe | Migrations 31 and 33-42 plus `master_persistence.py` preserve primary keys and compatibility aliases. `test_master_persistence.py` and `test_migrations.py` cover fresh, upgrade, conflict refusal, rerun, and historical attribution. |
| Master runner is chat-only, hostile-conformance tested, and fails closed (ADR 3) | `RunnerSpec.master_chat_only`, `master_runner_conformance`, `codex_master_proxy.py`, read-only scratch, empty capabilities, and native-permission denial form the boundary. `test_master_runtime_conformance.py` and `test_master_runtime_security.py` cover hostile shell/file/browser/skill/MCP attempts. `test_runner_specs.py` covers installed-version rejection. |
| Unsupported runners are not selectable from the Master UI | `/api/runners/detect` now carries the server-owned `masterChatOnly` declaration. `MasterScreen` renders only declared adapters and preserves an old choice as disabled explanation. `test_runners.py` and `MasterScreen.test.tsx` cover the API and UI. Browser evidence recorded below shows only Codex selectable. |
| Master supervision does not duplicate Satpam authority | `MasterSupervisor` only claims eligible queued work; `satpam.py` remains recovery owner. `master_projection.py` produces idempotent Task, Attention, and Satpam summaries. `test_master_supervision_projection.py` covers durable projection, restart reconciliation, capacity, and attribution. |
| One shared provider and one durable SSE stream support popup and Home (ADR 8) | `MasterStateProvider` owns the canonical session, cursor, EventSource, draft, target, toast queue, and scroll state. `MasterPopup`, `MasterScreen`, and shared conversation/composer consumers are covered by `MasterStateProvider.test.tsx`, `MasterPopup.test.tsx`, and `MasterScreen.test.tsx`. |
| Target, toast, Focus, history, accessibility, responsive shell behavior | `master_focus.py`, `masterHistory.ts`, `MasterTargetPicker.tsx`, and `MasterToastRegion.tsx` keep server-owned immutable attribution. Unit tests cover reconnect, coalescing, target changes, history selection, keyboard behavior, and shell routing. Browser Home and popup snapshots were captured in disposable data. |
| Focus is a roving single thread, never a copied or spliced session (ADR 9) | Migrations 38-42, `master_focus.py`, and immutable message/run fields hold epoch, subject, target, and Area attribution. `test_master.py`, `test_master_persistence.py`, and `test_master_supervision_projection.py` cover pending Focus, restart, delayed output, deleted Containers, and exact history projection. |
| Scoped Graphify context is atomic, fresh, provenance-tagged, local-only by default, and independent of Live state (ADR 6) | `graph_context.py`, `context_router.py`, `code_graph_lifecycle.py`, and `knowledge_graph_lifecycle.py` use registered Container/Area ids rather than model paths. `test_graph_context.py`, `test_code_graph_lifecycle.py`, and `test_knowledge_graph_lifecycle.py` cover last-good retention, leakage, path refusal, freshness, and Live-state SQL reads. |
| Master and self-update feature boundaries remain disabled until their gates pass | `DEFAULT_CONFIG`, `scripts/smoke-fresh`, and `test_feature_flags.py` verify both flags default false and routes are inert before side effects. The isolated fresh-install smoke asserts the full published feature map. |
| Candidate build, clone, fixture, and browser checks cannot touch production | `apps/safe_updater/{candidate,candidate_data,fixture_assembler,sandbox,probe_runner}.py` enforce candidate-only paths, networkless limits, fake runner data, and trusted probes. `test_safe_update_candidate.py` covers clone migration, sentinel denial, resource limits, probe tampering, identity, cache, port, and browser scenario contracts. |
| Journal replay, phase kill, migration/WAL, writable proof, rollback, breaker, assets, and service adapters are fixture-proven | `SafeUpdateController`, `Journal`, `sqlite_image.py`, `write_fence.py`, `circuit_breaker.py`, and adapter contracts implement the transaction. `test_safe_updater_foundation.py`, `test_safe_update_switch.py`, `test_safe_update_process_containment.py`, and `test_safe_update_api.py` cover phase recovery, fence/drain, sealed images, WAL/SHM quarantine, maintenance authorizer, disposable write/delete, rollback, breaker, and manager fail-closed behavior. |
| No safe-update activation shortcut or signing authority exists | `scripts/proxima update`, normal installers, and unmanaged adapters refuse activation; `feature_safe_self_update` stays false. `docs/installation.md`, `docs/security-boundaries.md`, and `adding-safe-updater-adapter.md` describe the enrollment boundary. Tests use no privileged service, live pointer, live release, live data, or signing key. |
| API/schema/docs obligations stay synchronized | `scripts/gen_docs.py` now preserves an existing generated footer when the semantic API/schema body is unchanged, so its mandatory drift check is repeatable. `test_gen_docs.py` covers the regression. `apps/api/.venv/bin/python scripts/gen_docs.py` completes cleanly twice. |

## Browser evidence

All browser checks used `mktemp` runtime data, a local loopback server, a fixture
owner, and `feature_safe_self_update=0`.

| Check | Evidence |
| --- | --- |
| Master Home | Authenticated disposable browser run showed Focus, Roving/Fleet/Container history, target picker, work panel, one Master composer, and accessible control labels. Screenshot SHA-256: `b7122269936e5dab2d64236bfa6650324182cf068d4703ceec349cb89bbed22f`. |
| Chat-only selector | The browser observed a disabled `Claude Code (not qualified for Master)` current state and only enabled `Codex`. Screenshot SHA-256: `fe264f5cd6c89c545506a594d2c8d5af500e29f2f5626604223502e2feaca43a`. |
| Popup | From Chat, `Open Master popup` produced one modal Master conversation with Focus, target picker, close control, corner control, and `Open full Master home` bridge. |

## Required validation commands

Run these on the final committed branch before release handoff:

```bash
cd apps/api && .venv/bin/ruff check proxima_api tests
cd apps/api && .venv/bin/python -m pytest -q tests
npm --prefix apps/web test -- --run
npm --prefix apps/web run build
bash scripts/smoke-fresh
apps/api/.venv/bin/python scripts/gen_docs.py
git diff --check
```

The API suite includes the migration and historical-upgrade matrix; the web
build runs TypeScript checking and emits the production PWA assets.

The no-mistakes pipeline is the final repository review, push, PR, and CI gate.
