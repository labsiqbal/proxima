# Workflow Graph Engine

The workflow graph engine is Proxima's runner-agnostic, reviewable orchestration
path from [ADR-0001](adr/0001-workflow-execution-model.md). It is always on
(the feature-flag system was removed in prune A2, #129) and coexists with the
classic linear engine for existing data. Recipes opens the graph Editor
directly.

## What it provides

- An architect **slices** a chat into a typed directed acyclic graph (DAG) of jobs —
  a **plan** that is directly runnable (run-first, T2): no template has to be saved
  first, and saving one (a reusable Recipe) is an optional act before or after the run.
- Every sliced job carries its **work binding** (T1): a `target` naming one project
  code area or `ops`, with `touches_repo` derived from it. An ambiguous binding is a
  surfaced question that blocks start, never a runtime guess.
- The owner reviews, lays out, and edits the frozen plan before explicitly starting it.
- Every node attempt runs in a fresh hidden ACP session against the selected runner.
- Each node may name **its own agent**; nodes without one use the job's agent.
- Independent branches execute **in parallel**, bounded by a concurrency budget.
- An optional **trigger node** is the graph's entry point. It owns the shared
  **intake contract** for manual Run plus optional **schedule seed** settings
  (cron, IANA timezone, overlap, enabled default Off) that promote to independent
  schedule rows.
- A **script node** is a deterministic step (T6): it runs a saved script from the
  project's `scripts/` library as a subprocess — no LLM, no agent — under the same
  node state machine and dispatch budget, gated by a one-time hash-bound approval.
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
| `type` | `agent` (default), `trigger`, or `script`. Absent means `agent`, so graphs predating node types keep working. |
| `trigger_kind` | Trigger nodes only. Informational authoring-view label for the entry point; intake and schedule seeds are independent fields on the same trigger and survive panel toggles. |
| `inputs` | Shared run intake declaration: `{id, label, kind, required, default?}` fields whose values fill `{{id}}` placeholders on every manual **Run**. IDs begin with a letter and contain only letters, numbers, and underscores. |
| `schedule` | Optional schedule seed: `{cron, timezone, overlap_policy, enabled}` (enabled defaults Off; UI timezone defaults to the browser zone, API/graph omit defaults to UTC). Promoted to the workflow's schedule row whenever the object is present, regardless of `trigger_kind`. Unattended ticks resolve durable bindings rather than replacing intake. |
| `command` | Script nodes only (required). The library script this step runs - a path relative to the Container's physical `ops/scripts/` folder, canonicalized (`scripts/x.sh` ≡ `x.sh`) and jailed at normalization: `..`, absolute paths, and backslashes are rejected when the plan is frozen, not just at run time. |
| `args` | Script nodes only. CLI args, a list of strings; `{{var}}` placeholders fill from the job input at execution time (the same substitution instructions get). Whole-blank entries are dropped. |
| `expected_output` | Agent nodes only. Prose for what a good result is; reaches the runner as the prompt's EXPECTED OUTPUT. |
| `rules` | Agent nodes only. Prose constraints on *how* to do it. Omitted from the prompt entirely when unset. |
| `skill_ids` | Agent nodes only. Skill/tool hints listed in the prompt as suggestions — the node's agent profile still decides what is actually enabled. Deduped; absent when empty. |
| `profile_id` | Agent nodes only. The agent this node runs as; absent/null = the job's agent. |
| `target` | Agent nodes only. The ONE container area this job works against: a registered code area's rel_path (`.`, `apps/web`, …) or `ops`. Absent on pre-slice-3 plans, which keep their old behavior (no repo binding). |
| `touches_repo` | Agent nodes only. **Always derived** from `target` at normalization — an authored value is never trusted, because a lie here would let a repo job dodge its worktree. |
| `target_ambiguous`, `target_question` | Agent nodes only. The slicer could not decide where the job works; the question surfaces in the plan and start is refused (409) until the owner picks a target. Choosing a target IS the resolution: a node with a target cannot be ambiguous. |
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

A trigger carries declared **`inputs`** as
`{id, label, kind, required, default?}`. They are authored in the trigger inspector as
the shared reusable intake contract, and every manual **Run** from that template asks for
them first. New rows are valid, complete objects from their first write; label, ID, and
default edits stage within a stable-ID row and commit only when the complete row is valid.
Blank, malformed, and duplicate IDs remain local with an actionable field error. A
rejected graph PATCH leaves the previous contract intact, keeps the draft Not saved, and
exposes Retry without allowing execution. Optional **schedule seed** settings on the same
trigger do not replace intake: promoting a plan may create a schedule row (default Off),
and unattended ticks or **Run now** resolve durable schedule bindings instead of prompting.
`workflows.inputs` remains a compatibility projection for existing RunModal and API
consumers. New saves derive it from the trigger, while migration and read-time
hydration move legacy declarations onto the trigger without breaking `{{var}}`
references.

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
validated, frozen **job input** as its output, so downstream nodes receive that input as
ordinary typed upstream data rather than through a special case. The trigger always
exposes the shared intake-form editor for manual **Run**. Before the job can claim
`running`, the start API requires every declared required value, validates number and
URL fields, applies declared defaults, and removes blank optional values. Existing
job-owned values are preserved. Optional schedule seed fields (cron, IANA timezone,
overlap, enabled default Off; UI defaults timezone to the browser zone, API/graph omit
defaults to UTC) sit beside intake and promote to a schedule row without carrying
per-run intake values; unattended cadence and **Run now** resolve durable bindings so
they never prompt a human.

The point of modelling the entry point as a node is that future webhook and event
modes can become further `trigger_kind` values here, not a second execution path.

### Script nodes (deterministic steps, slice 6 / T6)

A script node runs a saved script from the Container's physical **`ops/scripts/` folder**
- no LLM, no agent process. It is "a new kind here, not a new execution path": the
same `node_states` row, the same `pending → ready → running → done/failed` walk, the
same concurrency budget, the same output validation. What differs is execution: the
dispatcher queues a **`runs.kind='wf_script_node'`** row (so the run shares the
worker's durability, turn quota, heartbeats, and crash reaping), and the worker hands
it to `script_runner.py` instead of a runner/ACP session.

At normalization a script node keeps `command`/`args`, its output contract, and an
optional `review_required` gate; agent-only fields (`profile_id`, `skill_ids`,
`expected_output`, `rules`) and the T1 work binding (`target`/`touches_repo`) are
forced off, the trigger's precedent. Scripts always execute with the **physical
Ops Area as cwd** and take no part in repo worktrees.

**I/O contract** (the simplest thing that works, no expression language):

- **in**: CLI args (with `{{var}}` filled from the job input) plus one JSON object on
  stdin — `{"job_input": {…}, "upstream": [{node_id, name, output_kind, output}, …]}`,
  exactly the typed hand-off an agent node receives in its prompt;
- **out**: stdout, validated against the node's `output_kind`/`output_schema` by the
  ordinary graph advancer (a script feeding a `json` contract must print one JSON
  value); exit code ≠ 0 fails the node with the code and a stderr tail, pausing the
  plan in review like any node failure. Output above 1 MB fails loudly rather than
  being truncated.

**Execution boundary (honest):** the script runs as the server user with an exec
*array* (never a shell string - args cannot inject through a shell), the physical
Ops Area as cwd, and a minimal environment (`PATH`/`HOME`/locale only, so the
server's own config/secrets env never reaches it). An executable file runs directly;
otherwise the extension picks the interpreter (`.sh`/`.bash` → bash, `.py` → the
server's Python, `.js`/`.mjs` → node). There is **no sandbox**: an approved script
can do anything the server user can. The trust model below is the control for that,
and it is an approval gate, not a jail — see `docs/security-boundaries.md`.

**Trust = content-hash binding (the captain's T6 decision).** Every execution hashes
the exact bytes about to run and compares them to the approved sha256 in
`script_trust` (one row per project + script path). First run — or any run after the
bytes changed — does **not** execute: the run fails with a structured
`script_approval_required:` error, the node pauses the plan in review, and the
inspector shows the one-time **Approve script & run** action
(`POST …/nodes/{node_id}/approve-script`). Approval re-reads the file and records the
hash of what is on disk *now* (never a hash from the request or the stored error),
writes an audit-log entry, and reruns the node through the ordinary stale/rerun path.
Unchanged trusted scripts then run with no per-run approval — the deterministic +
free payoff — and an edited script's hash mismatch forces re-approval. Both moments
are visible in the step's timeline (`script.approval.required`,
`script.trust.approved` events).

**Reuse awareness (the make-or-break):** agents write and maintain the library as
ordinary job output. Each script starts with a header comment block —
`# Description:` / `# Inputs:` / `# Outputs:`, one line each; no separate manifest.
`scripts_library.scan_catalog` parses those headers into a catalog (path + one-line
description) that is injected into **every project run preamble** alongside the wiki
catalog, with the instruction to prefer reusing/extending an existing script over
writing a new one. The plan slicer gets the same catalog and may emit script jobs for
steps needing no judgment — but only referencing scripts that exist; a step whose
script would first have to be written is an agent job.

### Output contracts

| Kind | Runner answer | Persisted value | Validation |
| --- | --- | --- | --- |
| `text` | plain assistant text | JSON string | must be text |
| `json` | JSON document | canonical JSON | valid JSON and optional `output_schema` |
| `artifact-ref` | JSON object or list of objects with `path` | canonical JSON | each path exists inside the job workspace |

Artifact references cannot escape the job workspace. A prompt or runner answer cannot
grant permission to read source, config, secrets, or unrelated paths.

### Per-job work binding (targets)

The architect prompt is told the project's registered code areas (T1's read surface)
and must bind every job to exactly one target — a code area when the job edits that
repo, `ops` for everything else — **at slice time, not at runtime**: the safe-copy
worktree must be cut from a known repo before any agent starts. When the target is
genuinely unclear, the slicer marks the job ambiguous with a question for the owner
instead of guessing.

Enforcement lives at two gates:

- **Plan create/edit** (`POST /api/graph/jobs`, `PATCH .../graph`): a target that
  names no registered code area (and is not `ops`) is a 422 naming the job, the bad
  target, and the areas that do exist. A plan with repo jobs but no project is a 422
  too — there are no code areas to bind to.
- **Plan start** (`POST .../start`): an unresolved target question is a 409 carrying
  the question(s); the owner answers it by picking a target in the node inspector's
  **Works in** field (the answer clears the question — one act, no separate flag).

Start also reserves the repo jobs' path: the plan's single code-area target is pinned to
`jobs.target_area_id` and the slice-2 worktree is cut *before* the plan claims
`running` (a refused cut — dirty repo, detached HEAD, no commits — is a 409 and the
plan stays queued). Phase-1 keeps **one worktree per plan**, so all repo jobs of a
plan must target the *same* code area; a multi-area plan refuses to start with a
split-the-plan message. During execution of a direct legacy plan the worker's cwd seam
is **node-aware**: a node runs in the worktree only when *it* touches the repo - its Ops
siblings run at the physical Ops Area, where their artifact outputs belong. A graph
Recipe run as a delegated Task instead inherits that Task's one selected Area for every
node (see [`task-delegation.md`](task-delegation.md)), and rejects a Recipe whose
explicit repo target disagrees with that Area. The final plan approve is the
merge point, with the same guarded `--no-ff` local merge and park-in-review-on-conflict
contract as a linear repo job.
Flag **off** (the escape hatch): targets are inert metadata, no worktree is cut, and
execution is exactly as before slice 3.

**Reviewing a repo plan's changes (slice 4):** the plan's diff surface lives on the
Tasks screen — the plan row expands into its job list plus a **Changes** section
(`components/tasks/ChangesReview.tsx`; T4's ratified language: expanding row and
full-width page, never a side panel or popup) showing the per-file list and unified
change from `GET /api/jobs/{id}/diff`. **Approve & merge changes** there (or on the
canvas — the same `POST /api/graph/jobs/{id}/approve`) runs the guarded local merge;
it is held while any plan job still awaits its own node review. A merge conflict
parks the plan in review with a plain needs-attention banner and a retry. **Reject…**
demands a one-line reason, then `POST /api/jobs/{id}/reject` fails the plan with
`rejected_reason` recorded and discards the isolated copy unmerged.

## Lifecycle

```text
chat promotion
  → architect DAG draft
  → queued graph job (autosaved human plan edit + layout)
  → Run dialog (required/type validation + defaults)
  → atomic start claim with resolved job input
  → trigger (if any) → done immediately, output = resolved job input, no run
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
- `runs.kind='wf_script_node'`: one subprocess attempt for one script node. The row
  keeps the job's profile/runner columns only so session rows stay well-formed —
  execution never touches a runner, and the worker branches on the kind;
- `script_trust`: the approved content hash per (project, script) behind the
  script-node trust gate;
- hidden `sessions.job_id`: a fresh ACP conversation for each attempt. A fresh session
  per node is also what lets branches run at once — `claim_run` serializes runs *per
  session*, so nodes sharing one session could never overlap.

A trigger node has a `node_states` row like any other, but no `runs` row and no
session: `node_states.run_id` stays null because nothing was executed.

State transitions use status/version/run-attempt guards. Late callbacks from a stale
attempt cannot overwrite a corrected or rerun node.

## Using the canvas

1. Open a chat and choose **To graph**. The architect result opens as a queued plan.
2. Inspect each node. While queued, edit its name, instruction, **Works in** target
   (the T1 binding; an open target question shows here and is answered by picking),
   **agent**, output contract, review gate, or dependencies; add/remove nodes; add a
   trigger; and drag nodes and connections. The draft autosaves, including layout.
3. Optionally choose **Save as Workflow**. Promotion is one click because the inline
   plan name and trigger contract already exist; category and description are optional
   metadata.
4. Choose **Run**. It is disabled until the graph and intake contract are valid,
   accepted by the server, and free of pending edits. The shared Run dialog collects
   and validates declared manual values before the start request.
5. Inspect live node state and validated outputs on the canvas.
6. When paused in review, choose **Approve node**, **Save correction**, or
   **Rerun node**. Complete the final **Approve final result** action.
7. Saved templates appear on the Workflows home. Manual Run opens the same validated
   dialog used by drafts, then creates the job and starts it with the resolved input.

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
prompt asks for `<workflow-graph>` containing
`{name, description, category, graph:{nodes[], edges[]}}`; a trigger node carries its
own `trigger_kind`, `inputs`, or `schedule`. It tells the agent that nodes with no edge between them run
at the same time, which is the whole reason to leave an ordered list behind.

The chat is pinned to the graph job's **own session** (`jobs.session_id`, created with the
job) rather than a second thread, so reopening a plan resumes its conversation.

`parseGraphDraft` drops what the server would reject anyway — nodes with no id, duplicate
ids, agent nodes with no instruction, self-edges, dangling and duplicate edges — rather
than letting one bad entry cost the whole reply. A reply with no graph block at all
returns null, so an ordinary conversational turn never disturbs the canvas. Hand-placed
`x`/`y` survive a redraw by node id, so asking for a change does not scatter a canvas the
owner has already arranged.

### Testing a node before approval

While a plan is still **queued**, every agent node's inspector has **Test in chat**: a
dry run in the workflow chat, the same move the linear editor's "run through step N"
made. The prompt inlines the node *and its upstream ancestors* (`testChainFor` — a
node's output only makes sense with its upstream context), in dependency order,
skipping triggers and unrelated branches. Known job input fills `{{id}}` values;
otherwise the agent is told to use sensible samples. The reply carries no graph block,
so a test can never redraw the canvas, and no job state is touched — approval stays a
deliberate, separate act. The prompt also declares the run a **rehearsal that produces the
real end result** — the owner judges actual output, not a description of it — under two
hard rules: the agent must **never modify or overwrite an existing project file** (that
is the unrecoverable case), and every file it creates must be **named as a test**
(`-test` before the extension, e.g. `design-test.html`), with a closing list of created
files so they are easy to find and delete. Test artifacts therefore do appear in
Artifacts, clearly labeled; the approved run still produces the real deliverables. Rehearsals
also **reuse each other**: tests run in the plan's one chat session, so the prompt tells
the agent to reuse an upstream step's result from earlier in the conversation when that
step's instruction is unchanged — testing the join node doesn't re-pay for branches
tested moments ago. (Real runs already have this: node outputs persist in `node_states`,
and a rerun re-executes only the node itself plus its stale descendants.)

### Panels, labels and @-mentions

The screen has **two stages**, Design Studio's shape: a browsable **home** and a
focused **editor** - browsing and editing are different modes of work. Home remembers
the last selected **Drafts**, **Workflows**, or **Runs** tab and uses tables so each list
can grow independently. Draft rows are queued and editable, runnable, or promotable to
a saved workflow. Workflow rows live in one reusable-workflow table with **Availability**
(active or paused) separate from the joined **Automation** summary (schedules on, off, or
needing bindings). Every row keeps Edit, manual **Run** (per-run intake when fields are
declared), **Schedules**, availability pause/resume, and archive. The schedule dialog owns
timezone, cron, durable bindings, overlap, per-schedule On/Off, Run now, configure, and
delete. Run rows show recency, status, duration, and a View
action. Opening anything lands in the editor: full-width canvas + workflow chat + node
inspector, a ← back to home, and no rail — the editor is about one workflow at a time.
Chat and inspector keep their **draggable widths** (persisted per panel); plan statuses
stay phrased as what the owner can do next ("Draft — editable", "Needs your review").
Opening or resizing either panel, expanding workflow metadata, selecting a node,
resizing the browser, or growing the graph refits the canvas from its measured SVG
viewport. Resize bursts share one update per animation frame. Fit mode keeps the whole
graph framed; after a deliberate pan or zoom the canvas preserves that preferred focal
point and scale wherever space allows and temporarily constrains them only enough to
keep every node visible. The transform update does not remount nodes, so selection and
keyboard focus stay put, and it has no animation to override reduced-motion
preferences.

The inspector's instruction / expected output / rules fields and the workflow chat all
support **@-mentions**: typing `@` offers the project's artifacts and inserts the picked
file's project-relative path — pointing a node at a real deliverable instead of
describing it. The inspector also carries a per-node **Skills** picker fed by the
effective agent's runner capabilities; picks land in `skill_ids` and reach the runner as
a SUGGESTED SKILLS/TOOLS line. (This revisits the earlier decision to skip per-node
skills: the owner wanted the hint surface back, and as *suggestions* it does not
conflict with the profile owning the real capability grant.)

### Screen layout

The canvas is the workspace; the chrome yields to it.

- **Header bar**: one shared `.tasks-head, .graph-header` rule (inherited from the
  retired Sequential mode, kept so list-shell surfaces cannot drift apart). Left to
  right: plan-list toggle, the project picker (the shared `Dropdown`), plan title, job
  status, node count, and passive **Saving… / Saved ✓ / Not saved** state. Saved means
  the current graph is the last version accepted by the API; pending edits, invalid
  intake fields, and rejected writes cannot display Saved. The title renames inline
  like a file. The draft footer owns exactly two plan-level actions: one-click
  **Save as Workflow** and **Run**. They live outside the node form because they act on
  the whole plan, which is also what allows the inspector to close.
- **Plan list** (plans, templates): collapsible from the header. It is navigation between
  plans, not something needed while authoring one. The project picker is deliberately *not*
  here — it belongs in the header bar with the other plan-level controls.
- **Canvas tools** (`+ Node`, `+ Trigger`): on the canvas, since adding a node is a canvas
  act and must not depend on a node being selected. Zoom sits opposite them.
- **Node inspector**: rendered **only while a node is selected**, with **×** to dismiss. It
  holds node-level config and the one node-level action, Remove node. A permanent column
  saying "select a node" would be furniture spending width the canvas could use.

A **new blank plan** starts from the rail's ＋ — a starter trigger wired to an empty
first step, with the authoring chat opened, since describing the workflow is the fastest
way to fill a blank canvas. (Sequential's "New workflow" retired with it; chat promotion
must not be the only door into the editor.) Template metadata the chat proposes, such as
name and description, rides along client-side and pre-fills the lightweight promotion
dialog. The trigger's shared intake contract and optional schedule seeds stay in the
graph and autosave with the plan. An empty Plan Chat explains this authoring relationship
- graph steps, branches, inputs, review gates, and node tests - rather than borrowing
main Chat's hands-on conversation and deliverables guidance.

A plan that has started is **frozen** — the job is the record of what ran, so its graph
cannot be redrawn after the fact. Its **outputs are not**: editing a node's output and
rerunning a node work while the job is paused in review *and after final approval* —
'done' is just an approved review, and a correction re-runs the affected slice the same
way either way (the job returns to running, descendants go stale, and it lands back in
review). Editing it again is one click: **Duplicate to edit**
creates a fresh queued copy of the frozen snapshot (positions and input included) and
opens it; the original stays as the run record. **Save template** is available on frozen
plans too — a proven run is exactly the thing worth templating.

Rail rows carry a hover-revealed **delete**: a plan delete removes the job, its node
states and every session the job owns (the main thread plus one per executed node — the
`sessions.job_id` FK is SET NULL, so the server sweeps them rather than leaving orphan
threads in the sidebar); a template delete removes the workflows row and its schedules
(a schedule for a deleted workflow could never run). Produced artifacts stay — they are
deliverables, not run records.

A job's **review opens where it can be acted on**: the Home attention strip, the Tasks
screen's plan rows, and a schedule's "Run now" pass the job's `engine` up, and a graph
job routes to this canvas instead of the linear TaskWorkspace, which has no way to
approve a graph gate.

### Tasks: plans + their jobs

The Tasks screen is the index of plans (T2): every graph plan appears alongside the
classic linear tasks, and a plan row **expands into its ordered job list** — name,
target badge (`repo`, a sub-path, `ops`, or a red `where?` for an unanswered target
question), touches-repo marker, and live status. List view and graph view are **two
projections of one plan**: branch-less plans read as a plain list; branching plans
offer the read-only canvas as a toggle, rendered by the same `GraphCanvas` component
the editor uses. Each plan row carries **Open plan** (the canvas, where editing and
review actions live) and **Save as Recipe** (the same save-template modal and
endpoint). A repo plan additionally shows its worktree state chip (`active`,
`merged`, `conflict`). With the graph feature off, the Tasks screen shows classic
tasks only, exactly as before.

Selecting a plan opens it showing the whole graph with nothing selected. The live poll
keeps an existing selection but drops one whose node has disappeared.

The screen paints no background of its own — `.main-pane`'s gradient is the app's
backdrop, and every other destination lets it through (the Recipes card grid, and even
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
the debounced autosave along with every other canvas edit. Pending work flushes before
leaving the editor or promoting the plan. Run cannot open until that flush has
completed successfully.

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
- `PATCH /api/graph/jobs/{id}/graph` - queued graph autosave plus inline title rename;
- `POST /api/graph/jobs/{id}/start` - explicit Run action; accepts an optional
  `{input: {...}}` body and atomically validates/freezes resolved manual intake before
  claiming execution;
- node output, rerun, and approval routes under `/nodes/{node_id}` (including the
  one-time script approval, `POST .../nodes/{node_id}/approve-script`);
- `POST /api/graph/jobs/{id}/approve` — final approval;
- `POST /api/graph/jobs/{id}/save-template` — reusable template.

| Layer | Files |
| --- | --- |
| Graph validation/readiness (incl. target tags) | `apps/api/proxima_api/graph.py` |
| Dispatch and prompt isolation | `graph_executor.py`, `workflows.py` |
| Typed advancement | `graph_advancers.py`, `worker.py` |
| Script library + deterministic execution (T6) | `scripts_library.py`, `script_runner.py` |
| Lifecycle/correction API | `routes/graph.py`, `state.py` |
| Repo-plan worktrees | `worktrees.py`, `routes/graph.py`, `worker.py` |
| Architect promotion | `routes/chat.py`, `run_drafts.py`, `workflows.py` |
| Canvas | `apps/web/src/screens/GraphScreen.tsx`, `components/workflows/GraphCanvas.tsx`, `screens/graphLayout.ts` |
| Tasks plan index | `apps/web/src/screens/ActivityScreen.tsx`, `components/tasks/planProjection.ts` |
| Typed client | `apps/web/src/api/graph.ts`, `types.ts` |

## Scheduling a graph

Schedules fire only for **`status='active'`** workflows - the tick and Run now both go
through the same spawn, so a paused workflow runs nowhere. The Workflows library presents
that workflow Availability separately from each schedule's **On / Off** state. Pausing a
workflow stops all of its schedules without changing those per-schedule choices. Every
row keeps an explicit manual **Run** action; declared trigger inputs open the per-run
intake dialog even when automation exists. Deleting remains the destructive path and
takes schedules with it.

A schedule is unattended. It stores a five-field cron, an explicit IANA timezone,
overlap policy, and durable bindings for workflow inputs. It never consumes a per-run
prompt or reuses answers from a manual run. `schedule_policy.py` derives the required
input contract from the graph trigger and is used by the API, scheduler, Run now, and
migration. A schedule with unresolved required inputs can be saved Off for configuration,
but cannot be turned On; the 422 `schedule_missing_sources` detail names the unresolved
fields and tells the owner to save a durable binding in Schedules before turning it On. The
scheduler checks again immediately before spawning so legacy or drifted unsafe rows fail
closed.

Cron matching happens in the schedule timezone. The scheduler's minute claim includes
the local UTC offset and timezone name, preserving once-per-minute behavior across
timezones and daylight-saving transitions. Migration 45 backfills existing rows with the
host timezone to preserve their former wall-clock behavior, locks schedule project
ownership to the workflow, and turns unresolved enabled schedules Off.

`POST /api/schedules` accepts a workflow of **either engine** (the linear-only
`_workflow_or_404` guard still protects the linear editor/iterate/job routes, so a graph
template cannot be edited or run as an ordered recipe). A schedule whose workflow row
carries a `graph` spawns an **`engine='graph'` job** - the
same frozen snapshot, `node_states` and executor a manual `POST /api/graph/jobs` +
`/start` produces, so a cron run and a manual run cannot drift apart. It used to build
`steps_state` from the template's `steps`, which is `'[]'` for a graph, and silently spawn
nothing.

`POST /api/schedules/{id}/run` uses that same resolver and spawn path without claiming the
cron minute. The UI awaits the returned graph job, verifies it belongs to the workflow's
owning project, selects that exact job, and closes the schedule dialog only after the
selection is confirmed. List refreshes and exact-job loads use separate request
generations, so refreshing schedule summaries cannot cancel the handoff.

Run the disposable Chromium regression with `npm run test:e2e:schedules`. It builds the
web app, starts an isolated API with synthetic data and a fake runner, verifies missing
binding refusal and reload behavior, then proves Run now opened the exact owning-project
job with its durable binding. CI runs this scenario assertion-only. Pass
`--screenshots <dir>` to capture the stable before/after PNGs
(`before-missing-binding`, `after-missing-binding-refusal`, `before-run-now`,
`after-run-now-exact-job`) and validate each as a nonempty PNG; durable evidence lives
under `docs/evidence/scheduled-workflow-trust/`.

## Compatibility boundary

The classic engine remains `engine='linear'` for **pre-existing** jobs and sessions, with
`steps_state`, one shared ACP session and classic Activity. Its authoring surface — the
Sequential mode, recipe form and recipe chat — is **retired**: new workflows are authored
only on the canvas, and a linear recipe is expressed as a graph with no branches.
`IterateStage` remains reachable from an old session that carries `workflow_id`. Graph
jobs and templates are listed by the graph API; `GET /api/workflows` still lists only
linear rows (`graph IS NULL`). The Workflows home resolves graph template names through
`GET /api/graph/templates` and joins them with schedule rows in the client.

See [adding-workflow-node-type.md](adding-workflow-node-type.md) before extending node
execution or output contracts.
