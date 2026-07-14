# Workflow Graph Engine

The workflow graph engine is Proxima's runner-agnostic, reviewable orchestration
path from [ADR-0001](adr/0001-workflow-execution-model.md). It coexists with the
classic linear engine and is default-off behind:

```bash
PROXIMA_FEATURE_WORKFLOW_GRAPH=0
```

Set the value to `1` and restart the API to expose graph planning, routes, worker
dispatch, and the **Workflow Graphs** navigation item. Leaving it off keeps graph
routes inert and prevents queued graph-architect or graph-node runs from reaching a runner.

## What it provides

- An architect turns a chat into a typed directed acyclic graph (DAG).
- The owner reviews and edits the frozen plan before explicitly starting it.
- Every node attempt runs in a fresh hidden ACP session against the selected runner.
- Upstream results are passed as explicit typed inputs, never implicit chat history.
- Node output is validated as `text`, JSON Schema-backed `json`, or a contained
  `artifact-ref` before downstream work can start.
- A failed or gated node pauses in review. The owner can correct its output, rerun
  only that node, or approve the gate.
- Correcting or rerunning an upstream node marks every transitive descendant
  `stale`, then deterministically recomputes that affected subgraph.
- A reviewed graph can be saved as a reusable workflow template.

Phase 1 deliberately dispatches only one ready node at a time. A diamond is a real
DAG and has durable branch state, but its branches do not execute concurrently yet.
Bounded parallel execution is Phase 2.

## Graph data contract

A graph is frozen on each `engine='graph'` job:

```json
{
  "nodes": [
    {
      "id": "research",
      "name": "Research",
      "instruction": "Collect verified facts",
      "output_kind": "json",
      "output_schema": {
        "type": "object",
        "required": ["facts"]
      }
    },
    {
      "id": "write",
      "name": "Write brief",
      "instruction": "Write from the verified facts",
      "output_kind": "text",
      "review_required": true
    }
  ],
  "edges": [{ "from": "research", "to": "write" }]
}
```

Node IDs are unique, edges must reference existing nodes, self-edges and cycles are
rejected, and edges are the canonical dependency representation. Planner input may
use `depends_on`; normalization converts it to edges and removes it from nodes.

### Output contracts

| Kind | Runner answer | Persisted value | Validation |
| --- | --- | --- | --- |
| `text` | plain assistant text | JSON string | must be text |
| `json` | JSON document | canonical JSON | valid JSON and optional `output_schema` |
| `artifact-ref` | JSON object or list of objects with `path` | canonical JSON | each path exists inside the job workspace |

Artifact references cannot escape the job workspace. A prompt or runner answer cannot
grant permission to read source, config, secrets, or unrelated paths.

## Lifecycle

```text
chat promotion
  → architect DAG draft
  → queued graph job (human plan edit gate)
  → Approve plan & start
  → pending → ready → running → done
                         ├─ review gate → review → approve → done
                         └─ invalid/error → failed + job review
  → final review
  → Approve final result → done
```

The durable state is split between:

- `jobs.graph`: frozen graph snapshot and graph-level status;
- `node_states`: node status, resolved inputs, validated output, run attempt,
  checkpoint, and optimistic `version`;
- `runs.kind='wf_node'`: one runner activity for one node attempt;
- hidden `sessions.job_id`: a fresh ACP conversation for each attempt.

State transitions use status/version/run-attempt guards. Late callbacks from a stale
attempt cannot overwrite a corrected or rerun node.

## Using the canvas

1. Enable the flag and restart Proxima.
2. Open a chat and choose **To graph**. The architect result opens as a queued plan.
3. Inspect each node. While queued, edit its name, instruction, output contract,
   review gate, or dependencies; add/remove nodes; then choose **Save plan**.
4. Optionally choose **Save template**.
5. Choose **Approve plan & start**. This is the mandatory human execution gate.
6. Inspect live node state and validated outputs on the canvas.
7. When paused in review, choose **Approve node**, **Save correction**, or
   **Rerun node**. Complete the final **Approve final result** action.
8. Saved templates appear in the canvas sidebar; select one to create a fresh queued
   graph job and review its new frozen snapshot before starting.

The SVG canvas uses deterministic topological columns from `graphLayout.ts`; Proxima
does not add a workflow graph UI dependency.

## API and code map

Exact routes are generated in [reference/api.md](reference/api.md). The main surfaces
are:

- `POST/GET /api/graph/jobs` — create/list;
- `GET /api/graph/templates` — list reusable graph-backed workflows;
- `GET /api/graph/jobs/{id}` — inspect graph and node state;
- `PATCH /api/graph/jobs/{id}/graph` — queued-plan edit;
- `POST /api/graph/jobs/{id}/start` — explicit execution approval;
- node output, rerun, and approval routes under `/nodes/{node_id}`;
- `POST /api/graph/jobs/{id}/approve` — final approval;
- `POST /api/graph/jobs/{id}/save-template` — reusable template.

| Layer | Files |
| --- | --- |
| Graph validation/readiness | `apps/api/proxima_api/graph.py` |
| Dispatch and prompt isolation | `graph_executor.py`, `workflows.py` |
| Typed advancement | `graph_advancers.py`, `worker.py` |
| Lifecycle/correction API | `routes/graph.py`, `state.py` |
| Architect promotion | `routes/chat.py`, `run_drafts.py`, `workflows.py` |
| Canvas | `apps/web/src/screens/GraphScreen.tsx`, `graphLayout.ts` |
| Typed client | `apps/web/src/api/graph.ts`, `types.ts` |

## Compatibility boundary

The classic engine remains `engine='linear'`, with `steps_state`, one shared ACP
session, classic Activity, schedules, and existing workflow iteration. Graph jobs and
templates are listed only by the graph API and canvas; classic workflow lists,
dashboards, direct job creation, iteration, and scheduling exclude graph templates.
The Iterate screen explicitly requests a linear architect draft even when the graph
feature is enabled, so saving changes to an existing linear recipe cannot accidentally
convert it to a graph.

See [adding-workflow-node-type.md](adding-workflow-node-type.md) before extending node
execution or output contracts.
