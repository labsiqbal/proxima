# ADR-0001: Workflow execution model — own the orchestration primitives

- Status: Accepted
- Date: 2026-07-14

## Context

Proxima needs a **workflow** capability: run a multi-step process where the steps are
performed by AI agents, but the process is **controllable, inspectable, and correctable** —
the space between rigid RPA (n8n/Zapier: deterministic, not agentic) and fully autonomous
agents (agentic, but unpredictable and non-reproducible).

The target experience:

- **Dynamically planned.** An architect agent proposes the workflow shape (which steps,
  parallel or sequential) from the goal — the human is not forced to hand-build a graph.
- **Reviewable + fixable per node.** If one node is broken or its output is poor, the user
  can inspect it, edit its output, or re-run *that node*, like pinning/editing a node in
  n8n — without re-running the whole flow.
- **Runner-agnostic.** Work is executed by whatever runner the user owns (Claude Code,
  Codex, Gemini, local models) over ACP. No dependence on one runner's proprietary features.
- **Future-proof.** Runners and models change fast; the workflow foundation must not need
  rewriting each time they do.

The existing job engine does **not** deliver this. An audit of the current code found five
structural conflicts (see *Options → Option A*): steps share one ACP session (context is
carried implicitly, not passed as typed data), execution state is a linear `steps_state`
cursor with no node identity, output is unstructured assistant text (artifacts inferred by
filesystem mtime), there is a hard per-session serialization plus a global concurrency of 2,
and there is no per-node checkpoint/retry — one failed step fails the whole job.

## Decision drivers

1. **Own the stable core; quarantine the volatile edge.** What changes fastest (agent/model
   capability) belongs behind a stable interface (ACP + runner registry). What must stay
   solid (graph, state, I/O, review, scheduling) is Proxima's to own. This is the definition
   of "future-proof" here.
2. **Bring-your-own-agent (project DNA).** The core capability must not couple to one
   runner's features.
3. **Self-hosted, single-user, SQLite (project DNA).** No external orchestration servers or
   clusters.
4. **Reviewable determinism.** Non-determinism is fine *inside* a node; the process around
   it — inputs, outputs, order, checkpoints — must be inspectable and reproducible.

## Options considered

**Option A — Extend the current linear, single-session job engine.**
Cheapest to start. Rejected as the foundation: its core assumptions (one shared session,
history-based hand-off, a linear step cursor, text-only output) are the exact opposite of
node isolation and per-node re-run. It is a dead end for parallel/isolated/re-runnable work.

**Option B — Adopt LangGraph.**
Closest framework to the target (graph state, checkpointer, human-in-the-loop interrupts).
Rejected: LangGraph assumes the LLM call happens *inside your process*. Proxima drives
**external runner subprocesses over ACP** — a node is a prompt to another process, not an
in-process function. The model mismatch (plus a hard Python-in-process coupling) makes it
the wrong fit, though its *patterns* are worth borrowing.

**Option C — Adopt a durable-execution engine (Temporal / Inngest / Restate).**
The right *pattern* for durable, resumable, per-step-retryable execution. Rejected: they
require a server/cluster to run, which contradicts the self-hosted single-user DNA. Too
heavy for the deployment target.

**Option D — Delegate orchestration to the runner agent** (the agent plans and executes,
including parallelism via its own sub-agents; Proxima is only a checkpoint/review layer).
Lightest, and appealing as "cockpit above the terminal." Rejected **as the foundation**: it
couples the core capability to runner-specific features (Claude Code sub-agents), which
other runners lack — a direct violation of bring-your-own-agent, and the least future-proof
choice because a runner update can silently change behaviour. (It may return later as an
*optional* node type, not as the base model.)

**Option E — Build lightweight native orchestration primitives on SQLite, over the existing
`runs` machinery.** Proxima owns a small, durable graph engine; each node dispatches a run
against any runner. Chosen.

## Decision

Adopt **Option E**. Build Proxima's own lightweight orchestration layer on SQLite, borrowing
patterns from durable-execution engines (checkpointed state, per-step retry), LangGraph
(explicit graph state, human-in-the-loop interrupts), and data-DAG orchestrators
(dirty-propagation re-run), **without adopting any of them as a dependency.**

**Core principle: Proxima owns the orchestration primitives; the runner owns the
intelligence.** As runners get smarter, each node gets better, but the primitives never need
rewriting.

The six primitives Proxima owns:

1. **Graph as data** — nodes + edges (dependencies), stored as JSON/rows; the topology is
   inspectable and editable.
2. **Durable per-node state** — a real per-node record (inputs, output, status, checkpoint),
   not a linear cursor.
3. **Structured I/O contract** — a node declares, emits, and validates a typed output
   (`text | json-schema | artifact-ref`); downstream nodes consume it as data, not as
   scraped chat history.
4. **Runner-agnostic node execution** — one node = one run with explicit inputs injected,
   against any ACP runner. The run is treated as a durable "activity."
5. **Review / correction protocol** — inspect, edit-output, re-run-node, with
   **dirty-propagation** (correcting a node invalidates its downstream).
6. **Scheduler abstraction** — dispatch nodes whose dependencies are satisfied, respecting a
   concurrency budget. Starts bounded; grows without re-architecture.

Execution sophistication is **phased on top of these primitives** so value ships early
without a throwaway foundation:

- **Phase 1 — sequential on the new primitives.** Topological execution, concurrency 1–2,
  per-node review + edit-output + re-run. Delivers the reviewable/fixable experience. *(Not
  the old linear engine — it runs on the new node/state model, so it is not thrown away.)*
- **Phase 2 — real parallel execution** (bounded, machine-limited).
- **Phase 3 — complex joins, deterministic (non-agent) node types (the "n8n" part),
  dynamic re-planning.**

The dynamic-planning experience is layered as: **architect agent proposes a graph → human
reviews/edits the plan → execute as a reviewable node graph → optionally save the approved
graph as a reusable template.** Dynamism lives at plan time (behind a human gate);
reproducibility lives at execute time.

The new engine **coexists with** the current linear job engine (which remains for simple
single-task runs) and supersedes it for multi-step workflows over time.

## Consequences

**Positive**

- Runner-agnostic and update-resilient: runner/model changes touch only node execution
  (the volatile edge), never the graph/state/review primitives (the stable core).
- No heavy external dependency; fits self-hosted single-user SQLite.
- Per-node inspect / edit / re-run — the "fix one node like n8n" capability that motivated
  the feature.
- Establishes the reusable pattern for how features are added to Proxima (primitives first,
  features phased on top).

**Negative / accepted trade-offs**

- **Significant upfront investment.** The six primitives (schema, contracts, runner-agnostic
  executor, review protocol) are front-loaded work — measured in weeks, not days. This is
  the deliberate price of not needing a re-architecture later.
- **Parallelism is modest, not massive.** Driving local runner subprocesses on one machine
  bounds concurrency (default 2, machine-limited). Suitable for the use case; not "100
  agents at once."
- **Isolation has a cost.** Node isolation means a node runs with explicitly-injected inputs
  rather than free shared conversation context — more prompt assembly, and a session/prompt
  per node.
- **Non-determinism remains inside nodes.** "Fixing" a node means either re-running it
  (a new, possibly different result) or a human editing its output. Both must be supported;
  this is exactly where the reviewable model pays off.

## Related

- Superseded by: —
- Supersedes: — (first ADR)
- Feature docs to follow: a workflow feature doc + an "adding a node type" extension
  playbook, per the documentation contract in `AGENTS.md`.
