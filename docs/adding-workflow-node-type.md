# Adding a Workflow Node Type or Output Contract

This playbook preserves the invariants of the graph engine described in
[workflow-graph.md](workflow-graph.md) and
[ADR-0001](adr/0001-workflow-execution-model.md).

## Before changing code

Phase 1 has one execution type: a runner-agnostic **agent node** dispatched as a
`wf_node` run. `output_kind` is its data contract, not its execution type. Do not hide
a shell step, HTTP action, or runner-specific subagent behind a new output kind.
Deterministic execution types belong to Phase 3 and must still use the durable node
state machine.

Write down:

1. whether the proposal changes execution behavior or only output validation;
2. its canonical JSON shape and backward-compatible default;
3. how it remains runner-agnostic;
4. what can fail and which state receives that failure;
5. how correction and rerun invalidate descendants;
6. what a user can inspect and edit before execution.

Stop and write an ADR if the proposal adds an orchestration server, couples the core
to one runner, weakens node isolation, or bypasses the review/correction protocol.

## Invariants that must remain true

- Graph validation rejects unknown references, self-edges, duplicates, and cycles.
- The graph frozen on a job is canonical and independent of later template edits.
- Every agent-node attempt has a fresh hidden session and explicit upstream inputs.
- Only validated output can satisfy a downstream dependency.
- Node and job transitions use guarded status/version/run-attempt comparisons.
- A late stale callback cannot mutate current state.
- Correcting or rerunning a node invalidates every transitive descendant.
- Artifact paths resolve inside the job workspace and must already exist.
- The master feature flag rejects routes and queued graph architect/`wf_node` work
  before side effects.
- The classic linear engine and its API/UI remain unchanged.

## Adding an execution node type

1. **Extend the graph schema.** Add an explicit canonical field such as
   `execution_kind` in `graph.py`; validate a small allowlist and default old graphs to
   `agent`. Keep derived data out of the frozen graph.
2. **Define typed inputs and output.** Reuse the existing edge resolution and output
   contract. If the node consumes extra configuration, validate it during
   normalization, not after dispatch.
3. **Dispatch through one boundary.** Branch in `graph_executor.py` behind a small
   executor interface. Agent work must still use the runner registry/ACP. A
   deterministic executor must return a durable attempt identity and must not mutate
   node state outside guarded helpers.
4. **Persist the attempt.** Keep `node_states.run_id` semantics clear. If a
   non-runner attempt needs another durable table/key, add it additively and document
   migration/rollback behavior.
5. **Advance once.** Route completion through `graph_advancers.py`; validate output,
   perform one guarded transition, and dispatch the next ready node only after commit.
6. **Handle failure as correctable.** Persist a useful node error and pause the graph
   job in review. Do not terminally destroy the plan or its prior outputs.
7. **Expose inspection and correction.** Update graph API payloads and
   `GraphScreen.tsx` so configuration, attempt state, output, errors, rerun, and review
   are visible. Use design tokens and CSS classes; do not inline a new visual system.
8. **Keep the flag authoritative.** Add any new run kind to
   `features.queued_run_feature` before runner setup.

## Adding an output contract

1. Add the canonical kind to `parse_output_contract` in `graph.py` and the frontend
   `GraphOutputKind` union.
2. State the runner answer format in the node prompt assembled by
   `graph_executor.py`/`workflows.py`.
3. Add strict canonicalization in `validate_node_output` in `graph_advancers.py`.
   Convert once; downstream nodes and corrections must receive the same value shape.
4. Update `routes/graph.py::corrected_value` so human edits use identical validation.
5. Add the contract to the queued-plan inspector and render persisted output safely.
6. Document security boundaries, especially for paths, URLs, code, or secrets.

## Required tests

At minimum add tests for:

- normalization accepts valid configuration and rejects malformed/unknown values;
- graph cycle/readiness behavior remains unchanged;
- deterministic Phase 1 dispatch order and fresh agent sessions;
- valid output canonicalization and invalid output pausing in review;
- correction uses the same validator as runner output;
- stale callback rejection after correction/rerun;
- full transitive dirty propagation;
- feature-off route and queued-run rejection;
- classic linear job characterization;
- frontend typecheck, layout/inspector behavior, and feature-gated navigation.

If the contract produces artifacts, include a prompt-to-artifact test with an existing
contained path and rejection cases for missing and escaping paths.

## Verification

From the repository root:

```bash
cd apps/api
.venv/bin/python -m pytest -q tests
cd ../web
npm test
npm run build
cd ../..
git diff --check
apps/api/.venv/bin/python scripts/gen_docs.py  # routes/schema changes only
```

Then run diagnostics on every changed source file and manually exercise:

1. feature off: graph navigation absent and graph API returns 503 without writes;
2. feature on: architect draft → queued plan edit → explicit start;
3. node success/failure/review → correction or rerun → descendant recomputation;
4. final approval and optional template save;
5. one classic linear workflow from start through completion.

Update `workflow-graph.md`, `CAPABILITIES.md`, architecture/feature-map, and generated
API/database references in the same commit when their truth changes.
