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
- The owner reviews, lays out, and edits the frozen plan before explicitly starting it.
- Every node attempt runs in a fresh hidden ACP session against the selected runner.
- Each node may name **its own agent**; nodes without one use the job's agent.
- Independent branches execute **in parallel**, bounded by a concurrency budget.
- An optional **trigger node** is the graph's entry point. Only `manual` exists today.
- Upstream results are passed as explicit typed inputs, never implicit chat history.
- Node output is validated as `text`, JSON Schema-backed `json`, or a contained
  `artifact-ref` before downstream work can start.
- A failed or gated node pauses in review. The owner can correct its output, rerun
  only that node, or approve the gate.
- Correcting or rerunning an upstream node marks every transitive descendant
  `stale`, then deterministically recomputes that affected subgraph.
- A reviewed graph can be saved as a reusable workflow template.

## Concurrency

`dispatch_ready` queues **every** node whose dependencies are satisfied, so the two
branches of a diamond run at the same time. Two separate limits apply, and the
smaller one wins:

| Setting | Default | Meaning |
| --- | --- | --- |
| `graph_node_concurrency` (`PROXIMA_GRAPH_NODE_CONCURRENCY`) | 4 | Nodes of one graph job dispatched at once |
| `run_worker_concurrency` (`PROXIMA_RUN_WORKER_CONCURRENCY`) | 2 | Runs the worker executes at once, across all of Proxima |

Dispatching is not executing: node runs are queued into the ordinary `runs` table and
executed by `RunWorker`, so **`run_worker_concurrency` is the real ceiling**. Raising
the graph budget alone will not widen a fan-out. Both are bounded by the machine —
each concurrent node is another runner subprocess (see ADR-0001, *Parallelism is
modest, not massive*).

Because branches overlap, a review gate or failure on one branch pauses the **job**
while its siblings are still running. Those in-flight nodes are still allowed to
finish and persist their output; what stops is pulling *new* work forward. Corrections
wait until no node is `ready`/`running` (`ensure_reviewable`), so a paused job settles
before it can be edited.

## Graph data contract

A graph is frozen on each `engine='graph'` job:

```json
{
  "nodes": [
    {
      "id": "start",
      "type": "trigger",
      "trigger_kind": "manual",
      "name": "When I run it",
      "x": -290,
      "y": 40
    },
    {
      "id": "research",
      "type": "agent",
      "name": "Research",
      "instruction": "Collect verified facts",
      "output_kind": "json",
      "output_schema": {
        "type": "object",
        "required": ["facts"]
      },
      "profile_id": 3,
      "x": 40,
      "y": 40
    },
    {
      "id": "write",
      "name": "Write brief",
      "instruction": "Write from the verified facts",
      "output_kind": "text",
      "review_required": true
    }
  ],
  "edges": [
    { "from": "start", "to": "research" },
    { "from": "research", "to": "write" }
  ]
}
```

Node IDs are unique, edges must reference existing nodes, self-edges and cycles are
rejected, and edges are the canonical dependency representation. Planner input may
use `depends_on`; normalization converts it to edges and removes it from nodes.

### Node fields

| Field | Meaning |
| --- | --- |
| `type` | `agent` (default) or `trigger`. Absent means `agent`, so graphs predating node types keep working. |
| `trigger_kind` | Trigger nodes only. `manual` is the only kind today. |
| `expected_output` | Agent nodes only. Prose for what a good result is; reaches the runner as the prompt's EXPECTED OUTPUT. |
| `rules` | Agent nodes only. Prose constraints on *how* to do it. Omitted from the prompt entirely when unset. |
| `profile_id` | Agent nodes only. The agent this node runs as; absent/null = the job's agent. |
| `x`, `y` | Canvas position. Absent until the node is dragged, which is what lets un-placed nodes stay auto-laid-out. |

`expected_output` and `rules` are the per-step detail a linear recipe carried, and they
are prose for the runner — `output_kind`/`output_schema` stay the enforced contract. Both
are stored **absent rather than empty**: a blank field is not a constraint, and a bare
`RULES:` heading reads as a real instruction that invites a runner to invent its own.

`{{var}}` placeholders in `instruction`, `expected_output` and `rules` are filled from the
job input by the same `workflows.substitute` a linear step uses. An undeclared placeholder
is left visible rather than silently blanked, so a missing input shows up instead of
vanishing. The whole input is still handed to the node as typed data in
`<workflow_input>` — substitution is for writing readable instructions, not for hand-off.

`skill_ids` is deliberately **not** ported from linear steps. A node names its own agent,
and a profile already carries its skill/MCP selection — a second picker on the node would
be a second answer to the same question. Choosing the agent is choosing the tool surface.

A graph template carries declared **`inputs`** in the same shape a linear recipe does
(`{id, label, kind, required}`), stored exactly as declared by the same rule the linear
route uses. They are authored when a plan is saved as a template — the moment its reusable
contract is defined — and a run created from that template asks for them first, because a
node's `{{var}}` is useless if nothing filled it in.

`profile_id` is only a reference. `graph.py` does no I/O, so whether the profile exists
and belongs to the job's owner is checked by the executor at dispatch time — a node
naming an agent that is gone fails loudly rather than silently running as the job's
agent, which would return a plausible answer from the wrong agent.

### The trigger node

A trigger is the graph's entry point and is validated as one: **at most one per graph**,
and it may have **no incoming edges** (an entry point that waited on upstream work would
be a contradiction). Its contract is fixed rather than authored — it is forced to
`output_kind: "json"` and drops `profile_id`/`review_required`/`output_schema`.

It resolves without a runner: `dispatch_ready` completes it immediately with the
approved **job input** as its output, so downstream nodes receive that input as
ordinary typed upstream data rather than through a special case. A manual trigger *is*
the owner pressing start, so there is no work to do.

The point of modelling the entry point as a node is that `schedule`, `webhook`, and
`event` become further `trigger_kind` values here — not a second execution path.

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
  → queued graph job (human plan edit + layout gate)
  → Approve plan & start
  → trigger (if any) → done immediately, output = job input, no run
  → every ready node, up to the concurrency budget, in parallel:
      pending → ready → running → done
                           ├─ review gate → review → approve → done
                           └─ invalid/error → failed + job review
  → final review
  → Approve final result → done
```

A trigger walks that same `pending → ready → running → done` path rather than jumping
straight to `done`: skipping states would need a `pending → done` edge in the node state
machine, and that hole would then exist for every node. The intermediate states never
leave the dispatch transaction.

The durable state is split between:

- `jobs.graph`: frozen graph snapshot and graph-level status;
- `node_states`: node status, resolved inputs, validated output, run attempt,
  checkpoint, and optimistic `version`;
- `runs.kind='wf_node'`: one runner activity for one node attempt, carrying that
  node's own agent (`profile_id`/`runner_id`/`model`), not necessarily the job's;
- hidden `sessions.job_id`: a fresh ACP conversation for each attempt. A fresh session
  per node is also what lets branches run at once — `claim_run` serializes runs *per
  session*, so nodes sharing one session could never overlap.

A trigger node has a `node_states` row like any other, but no `runs` row and no
session: `node_states.run_id` stays null because nothing was executed.

State transitions use status/version/run-attempt guards. Late callbacks from a stale
attempt cannot overwrite a corrected or rerun node.

## Using the canvas

1. Enable the flag and restart Proxima.
2. Open a chat and choose **To graph**. The architect result opens as a queued plan.
3. Inspect each node. While queued, edit its name, instruction, **agent**, output
   contract, review gate, or dependencies; add/remove nodes; add a trigger; drag
   nodes and connections; then choose **Save plan**.
4. Optionally choose **Save template**.
5. Choose **Approve plan & start**. This is the mandatory human execution gate.
6. Inspect live node state and validated outputs on the canvas.
7. When paused in review, choose **Approve node**, **Save correction**, or
   **Rerun node**. Complete the final **Approve final result** action.
8. Saved templates appear in the canvas sidebar; select one to create a fresh queued
   graph job and review its new frozen snapshot before starting.

### Canvas interaction

Modelled on n8n so the gestures do not have to be guessed:

| Gesture | Result |
| --- | --- |
| Drag a node | Moves it; the position is saved on the node as `x`/`y` |
| Drag empty canvas | Pans |
| Wheel / **+** / **−** / **⤢** | Zooms; **⤢** frames the whole graph |
| Drag from a node's right handle onto another node | Creates a connection |
| Click a connection, then **×** | Removes it |

### Authoring by chat

The canvas has the same authoring chat the linear editor has, and it obeys the same
standing rule: **the agent edits the plan on screen, never the database.** The plan is in
front of the owner, so a background write would leave it stale and let the next Save undo
the agent's work.

What differs from the recipe chat is only the schema and where the reply lands, so both
share one `AuthoringChat` component and inject their own prompt/parse/apply. The graph
prompt asks for `<workflow-graph>` — `{name, description, category, inputs[],
graph:{nodes[], edges[]}}` — and tells the agent that nodes with no edge between them run
at the same time, which is the whole reason to leave an ordered list behind.

The chat is pinned to the graph job's **own session** (`jobs.session_id`, created with the
job) rather than a second thread, so reopening a plan resumes its conversation.

`parseGraphDraft` drops what the server would reject anyway — nodes with no id, duplicate
ids, agent nodes with no instruction, self-edges, dangling and duplicate edges — rather
than letting one bad entry cost the whole reply. A reply with no graph block at all
returns null, so an ordinary conversational turn never disturbs the canvas. Hand-placed
`x`/`y` survive a redraw by node id, so asking for a change does not scatter a canvas the
owner has already arranged.

### Screen layout

The canvas is the workspace; the chrome yields to it.

- **Header bar**: the *same* bar Sequential uses — one shared `.tasks-head, .graph-header`
  rule, so the two modes cannot drift apart again. Left to right: plan-list toggle, the
  project picker (the shared `Dropdown`, exactly as Sequential has it), plan title, job
  status, node count, unsaved marker; then the **plan-level** actions — Save template, Save
  plan, Approve plan & start. Plan actions live here rather than in the node form because
  they act on the plan, which is also what allows the inspector to close. The mode tab
  already names the screen, so the bar does not repeat it in a title block.
- **Plan list** (plans, templates): collapsible from the header. It is navigation between
  plans, not something needed while authoring one. The project picker is deliberately *not*
  here — it is the same control Sequential puts in the bar, so it belongs in the same place.
- **Canvas tools** (`+ Node`, `+ Trigger`): on the canvas, since adding a node is a canvas
  act and must not depend on a node being selected. Zoom sits opposite them.
- **Node inspector**: rendered **only while a node is selected**, with **×** to dismiss. It
  holds node-level config and the one node-level action, Remove node. A permanent column
  saying "select a node" would be furniture spending width the canvas could use.

Selecting a plan opens it showing the whole graph with nothing selected. The live poll
keeps an existing selection but drops one whose node has disappeared.

The screen paints no background of its own — `.main-pane`'s gradient is the app's
backdrop, and every other destination lets it through (Sequential's card grid, and even
Tasks' own `.job-list`). The graph screen used to paint an opaque shell *and* opaque
panels over it, which is why the gradient stopped dead under the mode tabs here and
nowhere else. The rail and inspector are delineated by their border, not by a fill.

A trigger node is the same shape and size as every other node; only its dashed stroke,
name and `manual` subtitle mark it as the entry point. It deliberately does not use the
accent tint, because that is the selection fill — an unselected trigger wearing it read as
permanently selected.

Layout is flex, not grid: the rail and the inspector each come and go, and flex simply
reclaims their space — a grid would need a column template per combination. Below 70rem
the inspector wraps under the canvas; below 52rem everything stacks.

Editing gestures are live only while the job is `queued` — the same window in which
`PATCH /graph` accepts a plan. Positions are part of the graph, so they are saved by
**Save plan** along with everything else, not written behind the owner's back.

A connection that would make the graph loop back on itself is refused on the canvas
with an explanation, rather than being sent to the server for a 422.

The **Dependencies** checkboxes in the inspector edit the same edges as drag-to-connect.
They are not redundant: the canvas gesture is pointer-only, and the list is how the
same edit is made by keyboard.

The SVG canvas is Proxima's own — no workflow graph UI dependency was added.
`graphLayout.ts` lays out nodes in deterministic topological columns as a *fallback*:
a node carrying `x`/`y` keeps its hand-placed position, since an architect draft
arrives with no coordinates and re-layering a node the owner deliberately moved would
undo that edit on every reload. The canvas is infinite, so the layout reports a real
bounding box — a hand-placed node may sit at negative coordinates, and anything that
frames the graph reads that origin rather than assuming `(0,0)`.

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

## Scheduling a graph

A schedule whose workflow row carries a `graph` spawns an **`engine='graph'` job** — the
same frozen snapshot, `node_states` and executor a manual `POST /api/graph/jobs` +
`/start` produces, so a cron run and a manual run cannot drift apart. It used to build
`steps_state` from the template's `steps`, which is `'[]'` for a graph, and silently spawn
nothing.

With `PROXIMA_FEATURE_WORKFLOW_GRAPH` off, a graph schedule is **skipped with a logged
warning** and its minute is still claimed: the executor would never dispatch the job, so
spawning one would leave a `running` job nothing advances, and not claiming the minute
would retry the same dead schedule every tick.

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
