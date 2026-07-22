# Adding a Workflow Node Type or Output Contract

This playbook preserves the invariants of the graph engine described in
[workflow-graph.md](workflow-graph.md) and
[ADR-0001](adr/0001-workflow-execution-model.md).

## Before changing code

There are three execution types today, all carried by the node's `type` field:

- **`agent`** (the default, and what an absent `type` means): runner-agnostic work
  dispatched as a `wf_node` run.
- **`trigger`**: the graph's entry point. It resolves inside `dispatch_ready` without a
  runner and has no `runs` row. `trigger_kind` selects the variety; only `manual`
  exists. `schedule`/`webhook`/`event` belong here as further kinds — that is the whole
  reason the entry point is a node.
- **`script`** (slice 6, T6 — ADR-0001's Phase-3 deterministic node in minimal form):
  runs a library script from the container's `scripts/` folder as a subprocess. It is
  the precedent for a deterministic type done right: dispatched as a `wf_script_node`
  run through the ordinary runs queue (durability, budget, quota, reaping), executed
  by `script_runner.py` behind the worker's kind branch, gated by the hash-bound
  approval in `script_trust`, and advanced by the same `graph_advancers` validation
  every node uses.

`output_kind` is a node's data contract, not its execution type. Do not hide a shell
step, HTTP action, or runner-specific subagent behind a new output kind. A new
deterministic execution type must still use the durable node state machine — follow
the script node's shape.

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

1. **Extend the graph schema.** Add the kind to the `type` allowlist in `graph.py`
   (`_NODE_TYPES`) and to the frontend `GraphNodeType` union; keep `agent` the default
   so graphs predating node types keep working. Validate the allowlist during
   normalization and keep derived data out of the frozen graph. Follow the trigger's
   precedent for fields a type does not use: force or drop them at normalization
   (a trigger fixes `output_kind` to `json` and drops `profile_id`/`review_required`)
   so no later stage has to ask whether a field is meaningful for this type.
2. **Define typed inputs and output.** Reuse the existing edge resolution and output
   contract. If the node consumes extra configuration, validate it during
   normalization, not after dispatch.
3. **Dispatch through one boundary.** Branch in `graph_executor.py` behind a small
   executor interface. Agent work must still use the runner registry/ACP. A
   deterministic executor must return a durable attempt identity and must not mutate
   node state outside guarded helpers.
4. **Persist the attempt.** Keep `node_states.run_id` semantics clear — a non-runner
   type leaves it null, as the trigger does. If such an attempt needs another durable
   table/key, add it additively and document migration/rollback behavior.
   **Walk the node state machine** (`state.NODE`) rather than widening it: the trigger
   resolves through `pending → ready → running → done` inside one transaction instead
   of adding a `pending → done` edge, because that edge would then exist for every node.
5. **Advance once.** Route completion through `graph_advancers.py`; validate output,
   perform one guarded transition, and dispatch whatever became ready only after commit.
   Remember that siblings run concurrently: a job paused in `review` must still accept
   an in-flight node's result, and only a `running` job may pull new work forward.
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
- parallel ready-set dispatch, the concurrency budget capping a fan-out, and fresh
  agent sessions per attempt;
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
