# Proxima — Capability Map

What's built, why it exists, and how it works. A reference for understanding what
this cockpit is actually capable of. (Derived from the code, not aspirational.)

> **Where do I edit when I add/change a feature?** See the companion
> [reference/feature-map.md](reference/feature-map.md) — a per-feature grid of
> code locations (backend + frontend), tables/events touched, relations, and
> status/flag. This doc explains *what & why*; that one maps *where*.

> **Model:** single-user cockpit. One owner, no in-app accounts. Network controls
> remain the primary access boundary, with a first-run owner password and authenticated
> bearer-token or HttpOnly-cookie sessions as defense in depth. Runtime data lives
> outside the repo (`~/.local/share/proxima/`).

---

## 1. Agents & runners (bring-your-own-agent)

**Why:** Proxima drives the AI agents you already own (Claude Code, Codex,
Hermes, and Pi) over ACP — no baked-in model.
**How:** `runner_specs.py` defines each runner's spawn argv + credential home +
wire `protocol`. The worker (`worker.py`) starts one agent subprocess per (runner,
home, cwd) on demand; `runner_spec(run.runner_id)` makes it runner-agnostic.
Most runners speak ACP; the **Codex** runner drives the owner's own
`codex app-server` (`codex_appserver.py`, same call surface) so it tracks the
current system Codex CLI instead of a stale bundled adapter core - newer models
like `gpt-5.6-sol` work as soon as `codex update` has them. Default resolves via
`default_runner()` (env → first *ready* runner → fallback).
Agent/app/script subprocesses share `subprocess_env` / `augmented_path`: common
user-local bins are appended, and when the host has `python3` but no `python`, a
workspace-local shim (`$PROXIMA_WORKSPACE_ROOT/shims/python` → python3) is
prepended so plan steps and scripts that still call `python` do not die with
command-not-found on Debian/Ubuntu-style hosts.
**Endpoints:** `GET /api/runners/detect` (installed/ready status).

## 2. Profiles (agent personas)

**Why:** Each profile = an agent persona with its own runner, isolated credential
home, default model, and system instructions ("soul").
**How:** `profiles` table (one owner, many profiles). `claude_live_home` mode points
claude-code at the real `~/.claude` so it inherits your skills/plugins/rules/memory.
**Skills & MCP (auto-detect per runtime):** each profile shows the skills + MCP servers
its runner actually has on this host — detected from the runner's own config dir
(`capabilities.py`, driven off `RunnerSpec.source_dir` so it's portable, no hardcoded
paths). The user checks which to enable per profile (`profiles.capabilities` JSON; NULL =
inherit all). Enabled skills are symlinked into the profile home and its MCP config is
filtered to the selection for Claude (`.claude.json`), Codex (`config.toml`), and
Hermes (`config.yaml`). Selection is re-applied idempotently before each run so newly
installed host skills appear automatically. Pi still uses its runner-global home;
`claude_live_home` profiles likewise use the host config directly.
**Endpoints:** `GET/POST /api/profiles`, `PATCH/DELETE /api/profiles/{id}`,
`GET /api/runners/{id}/capabilities`, `GET /api/tools/recommended`.

### Baked-in capability bundle (Phase-1 slice 9, T8 - LIVE)

**Why:** batteries-included instructions/skills, BYO credentials and binaries: every
Proxima user gets a curated skill set and work-discipline defaults without installing
anything, while Proxima never ships or manages binaries.
**How - three layers:**
- **Discipline pack (runner-agnostic, always on):** `GENERAL_GUIDE` in
  `wiki_memory.py` carries a compact "Work discipline" section (evidence-first,
  slice small, self-review before done, keep wiki memory current, prefer existing
  scripts) in every session preamble. Lean by design - a section, not a dump.
- **Bundled skills (runner-native, opt-out-able):** `bundled-skills/` at the repo
  root is a SECOND source in the existing skill-symlink mechanism
  (`capabilities.py:detect_bundled_skills`). Content-pluggable: any folder there
  with a `SKILL.md` is a skill - no list in code. Bundled skills get ids
  `bundled/<name>`, are symlinked into every profile home whose runner has a skills
  dir, and opt out per profile through the same `profiles.capabilities` selection
  JSON as host skills. First content: the owner's **masterplan** skill (vendored
  from labsiqbal/masterplan, MIT - see `bundled-skills/masterplan/PROVENANCE.md`).
  Live-home claude profiles are untouched (nothing seeded or symlinked - the user's
  real `~/.claude` rules). Config: `bundled_skills_dir` /
  `PROXIMA_BUNDLED_SKILLS_DIR` (default `<repo>/bundled-skills`).
- **Tools detect-and-advertise (never vendored):** `bundled-skills/recommended-tools.json`
  lists recommended host CLIs (markitdown, lavish-axi, gh - data, not code).
  `recommended_tools.py` probes PATH at run setup; PRESENT tools get a one-liner in
  the run preamble ("`markitdown` - available for …"). Missing tools surface only
  as a quiet hint in Settings → Agents ("Recommended tools" panel) and never block
  a run.

## 3. Chat (the core loop)

**Why:** Talk to an agent; it streams back, runs tools, asks for approvals.
**How:** A `session` holds messages; a `run` is one agent turn driving the ACP
session. The worker is bounded-concurrent (`run_worker_concurrency`) so one slow run
never blocks other chats. Tool permissions ask the owner by default; auto-approve is
an explicit Settings opt-in. Streaming uses SSE (`/events/stream`) + WebSocket.
**Endpoints:** `POST /api/chat/send`, `/api/sessions/{id}/runs`, `/messages`,
`/events/stream`, `WS /api/ws/sessions/{id}`, `/runs/{id}/cancel`, `/runs/{id}/permission`.

### Project-file and artifact references (`@`)

Every project-scoped prompt surface shares the same mention picker: Code chat, the Ops
Task Composer, workflow authoring chat and graph fields, Design Home, Design chat, and
Graph Home. Typing `@` filters a merged index of project files **and produced
artifacts** (designs, images, pages, docs, apps, videos, and other deliverables under
the conventional artifact roots) and inserts the selected reference at the caret;
arrow keys plus Enter/Tab work as well as the mouse. Ordinary files and non-image
artifacts become project-relative paths, which project-scoped runners can open from
their working directory. Images become `![name](path)` references, so `/image`
providers and design-agent vision receive the selected pixels instead of only a
filename. Produced artifacts carry a short kind badge (Design, Image, Doc, …) so they
are visually distinct from plain source files; artifacts are ranked ahead of the file
tree when the query is empty. The popup viewport shows four ranked matches at once;
additional matches remain available by scrolling, while typing more of the path or
title narrows the full bounded index.

The picker never expands text-file contents into the prompt. Its authenticated
`GET /api/projects/{slug}/reference-files` index is bounded and project-jailed, skips
symlinks, dependency/build/cache and hidden directories, and omits common secret/key
files. Produced artifacts come from the existing
`GET /api/projects/{slug}/artifacts` scan (year-long window, scanner-capped) and are
merged client-side by `useProjectMentionItems` so every Composer / `MentionTextarea`
surface stays on one pipeline. Vision loading re-validates paths against the session
project and accepts only bounded image files (10 files, 8 MB each, 32 MB total).

### Per-prompt Brainstorm / Debate modes

> **Status:** the `Brainstorm`/`Debate` chips are shown only in **Code chat**. The Ops
> Task Composer and the Design chat omit collaboration modes (tasks and design
> sessions are single-agent).

**Why:** Run a prompt through multiple agents before the answer lands in the
main chat, instead of validating a completed answer afterward.
**How:** The composer offers per-prompt `Normal`, `Brainstorm`, and `Debate`
chips. `Brainstorm` and `Debate` are sent as `prompt_mode` on
`/api/sessions/{id}/runs`; the user message is stored as typed (no mode prefix —
the cards and result title show the mode) and one parent busy run is shown in
the chat. `Brainstorm` fans out to the configured 2–3 profile-specific child
runs in parallel, then queues a synthesis pass. `Debate` runs the configured
2–4 rounds before a neutral synthesis/judge pass. While child agents work, the
chat shows inline cards labelled with the actual agent/profile name and
round/lane: collapsed by default (a cycling "thinking" shimmer while an agent
works, a 2-line preview when done), click to expand one at a time. Brainstorm
stacks cards vertically; Debate alternates them left/right per speaker so
rounds read as a conversation. The synthesis is NOT a card — it streams into
the parent bubble like a normal reply and lands as the final assistant message
(synthesis only; per-agent detail lives in the cards). Child runs still do not
save ordinary assistant messages; the card history replays from events.
Settings groups these defaults under **Agents & Collaboration**. The mode
resets after send, so there is no global Meeting Mode toggle.

### Message-level Validate sidecar

**Why:** Ask a different runner/profile to pressure-test a completed assistant reply
without polluting or advancing the main chat branch.
**How:** `Validate` creates a `message_reviews` sidecar row attached to the source
assistant message, queues a `kind='message_review'` run, streams review deltas to the
inline sidecar, then stores a structured verdict, gaps/risks, unanswered-input notes,
revised content, and suggested next move. The sidecar offers an `Auto` reviewer choice
(defaulting to a different runner) plus a local profile picker override. It can be
minimized to a compact summary. `Replace answer` overwrites the original assistant
message while preserving the original in the review row for `Restore original`.
`Ask source to merge` queues a `kind='message_review_merge'` run by the source profile
and writes its result back into the same sidecar, not as a normal chat message. Review
output is never saved as a normal assistant message unless explicitly replacing the
source answer. Brainstorm/Debate are intentionally not sidecar actions; they
live in the composer before a prompt is submitted.
**Endpoints:** `GET/POST /api/messages/{message_id}/reviews`,
`POST /api/message-reviews/{review_id}/replace-answer`,
`POST /api/message-reviews/{review_id}/restore-original`,
`POST /api/message-reviews/{review_id}/ask-original`.

## 4. Goal loop (multi-step autonomy)

**Why:** Give a goal; the agent keeps advancing across turns until done.
**How:** `/goal` sets an objective; the advance hook carries prior-step context.
Phase-1 note (T5): for repo work the plan/job path with timeout auto-continuation
(§8 Long work) supersedes the goal loop as the long-run mechanism; goal mode remains
a chat-side feature as-is (its timeout behavior is unchanged by slice 5).
**Endpoints:** `POST /api/sessions/{id}/goal`, `/goal/cancel`.

## 5. Chat → Wiki (knowledge continuity)

**Why:** Distill a conversation into a durable wiki note.
**How:** `wiki-note/draft` spawns a run that produces a `wiki.draft` event → preview
→ `wiki-note/commit` writes the markdown + rebuilds the wiki index.
**Endpoints:** `POST /api/sessions/{id}/wiki-note/draft`, `/wiki-note/commit`.

## 6. Chat → Plan (slice a goal into runnable jobs)

**Why:** Turn a conversation into a **directly runnable plan** — a DAG of jobs — not
just a saved recipe (run-first, recipe-later: T2).
**How:** `promote-workflow` has an architect agent slice the chat. The graph path is
enabled by default and emits a normalized `{nodes,edges}` DAG with typed outputs,
review gates, and **per-job work bindings** (Phase-1 slice 3, T1/T2): the prompt
carries the project's registered code areas, and every job is tagged with one `target`
(a code area or `ops`) plus the derived `touches_repo` marker — an unclear binding is
marked ambiguous with a question for the owner instead of a guess. The slicer is
explicitly instructed to size each job to complete within ONE turn quota (T5 slice 5:
continuation is the safety net, not the plan). The draft lands as
a queued plan the owner reviews/edits and starts directly; saving it as a reusable
Recipe is an optional, separate action (before or after the run). The feature flag
remains an owner recovery switch; the legacy ordered-step path is retained only for
existing data.
**Endpoints:** `POST /api/sessions/{id}/promote-workflow`.

## 7. Recipes (plans worth repeating) + schedules

**Why:** Codify a repeatable multi-step process the agent can execute — with branches,
per-node agents and review gates, not just a straight line. A saved template (Recipe)
is the **optional promotion of a plan** (run-first, recipe-later): plans run without
one, and "Save as Recipe" works before or after the run, from the canvas or from a
Tasks plan row.
**How:** authored on the **graph canvas** (see §Graph workflow engine): nodes carry
`instruction`, `expected_output`, `rules`, an optional per-node agent and review gate;
edges carry dependencies; `{{inputs}}` declared on the saved template are asked for at
run time and substituted into node text. An **authoring chat** beside the canvas emits
`<workflow-graph>` blocks that are applied to the plan on screen, never the database.
The **Sequential recipe editor is retired** — a linear recipe is a graph with no
branches. The linear engine remains for pre-existing jobs; `IterateStage` is still
reachable from an old session carrying `workflow_id`, but no new linear workflow can be
authored.
**Schedules** target saved graph templates: a due tick (or **Run now**) spawns the same
`engine='graph'` job a manual create + start produces — including repo isolation
(`target_area_id` + worktree cut via the shared `bind_graph_job_repo_worktree` path).
A refused cut fails the scheduled job in place with an owner-facing reason rather than
running unisolated against the live code area. With the graph feature flag off, a graph
schedule is skipped with a logged warning rather than left as a job nothing will advance.
**Endpoints:** graph routes (§Graph workflow engine), `GET/POST /api/schedules`,
`POST /api/schedules/{id}/run`; legacy linear rows keep `GET/PATCH /api/workflows/{id}`.

## 8. Tasks / jobs (executions)

**Why:** Every execution — a workflow run or an ad-hoc 1-step task — as one trackable
pipeline.
**How:** classic `engine='linear'` jobs use a frozen step snapshot and run
sequentially in one ACP session (context carries free). Graph jobs (**plans**) share
the job lifecycle but keep per-node state in `node_states` and are listed via the
graph API. Live-polls while running; auto-archive after 30 days. Old kanban tasks
were migrated to 1-step jobs.
**Endpoints:** `POST /api/jobs`, `/jobs/{id}/start`, `/jobs/{id}/link-run`, `/approve`, `GET /api/jobs[...]`.

### Tasks screen = plans + their jobs (Phase-1 slice 3, T2)

**Why:** One index of everything running or awaiting the owner — a sliced plan and a
one-off task are the same idea at different sizes.
**How:** the Tasks screen lists graph plans alongside classic tasks. A plan row
expands into its **ordered job list** (name, target badge, touches-repo marker, live
status); an unanswered target question shows as a `where?` chip and the plan cannot
start until it is answered. **List and graph are two projections of one plan**:
branch-less plans render as a plain list, branching plans offer the read-only
dependency canvas as a toggle (the editor's own `GraphCanvas`, reused). Plan rows
carry **Open plan** (the canvas, where review acts live) and **Save as Recipe**
(promotes the plan's graph to a reusable template via the existing save mechanics).
With the graph feature off, the screen shows classic tasks only, exactly as before.
**Endpoints:** `GET /api/graph/jobs` (+ the linear list above), `POST /api/graph/jobs/{id}/save-template`.

### Repo jobs: isolated worktrees + review + local merge (Phase-1 slices 2+4 - LIVE, on by default)

**Why:** A job that touches a repo must never edit the primary tree directly (T1). It
runs in a safe copy, the owner reviews the before/after diff, and approving merges the
work **locally** into the branch it was cut from - no remote, no GitHub required
(T1 local-first; the optional push-after-merge connector is the next section). **On by
default since slice 4 shipped the
review UI**; `feature_repo_worktrees` (`PROXIMA_FEATURE_REPO_WORKTREES`) remains the
owner's escape hatch - while off, job behavior is exactly as above.
**How:** a job may carry `target_area_id` - the ONE container area it works against
(T1: exactly one target; a code-area target = repo job). On start, `worktrees.py` cuts
branch `proxima/job-<id>` from the target code area's repo into a worktree under
`<workspace_root>/worktrees/job-<id>` - outside the container, so scans never see
work-in-progress and the worktree's `.git` file can't register as a code area. The cut
refuses loudly (409, job stays queued) on a dirty repo, detached HEAD, or no commits;
crash leftovers are cleaned idempotently by job id. With the flag on, the run worker
sets the run's cwd to the active worktree (a missing worktree fails the run loudly -
never a silent fallback to the primary tree). Diff and merge operate on commits:
outstanding edits are snapshotted onto the job branch first, so partial work also
survives crashes (feeds T5 continuation, slice 5). Snapshot commits drop runtime
cache/bytecode (`__pycache__`, `*.pyc`, pytest/mypy/ruff caches, …) even when the
target repo has no `.gitignore`, and the review diff hides those paths if an older
snapshot already committed them. Final approve = guarded `--no-ff`
merge: refuses a dirty repo or switched-away base branch, aborts on conflicts and
parks the job in `review` with the surfaced error (worktree kept; approve again after
resolving) - never forced. Success records `merge_commit` on the job's worktree row
(`job_worktrees`) and tears the worktree + branch down; deleting a job also tears its
worktree down.
**Endpoints:** `GET /api/jobs/{id}/diff` (per-file status + unified patch; also
readable after the merge), `POST /api/jobs/{id}/approve` (merge point),
`POST /api/jobs/{id}/reject` (see below), `POST /api/jobs` (`target_area_id`). Job
payloads carry a `worktree` object (branch, base, status
`active/merging/merged/conflict/discarded`, merge_commit, error) and, after a
rejection, `rejected_reason`.

**Graph plans (slice 3):** the same machinery wired per job-in-plan. With the flag on,
starting a plan with repo jobs pins their single code-area target to the job row and
cuts the worktree before the plan claims running (multi-area plans refuse to start —
Phase-1 is one worktree per plan); the worker runs each node in the worktree **only if
that node touches the repo** (ops jobs run at the project root), and the plan's final
approve is the merge point. Flag off: target tags are inert metadata and plans run
exactly as before.

**Known gap - tool approvals during plan-node runs (verified live 2026-07-22):** a
plan node's agent run uses the same permission machinery as chat, but its run thread
is a hidden job session, and no surface in the plan/Tasks UI renders the approval
card. An unanswered permission request times out after 300s and **cancels** (the
deliberate default: "no answer" never becomes "yes"; `PROXIMA_ACP_TIMEOUT_ACTION`
overrides), so with the default ask-each-time permission setting a plan job that
needs, say, a file-edit approval blocks honestly with the agent's blocker report and
can be rerun from the node inspector. Practical paths today: flip Settings → Agent
permissions to auto-approve (control then shifts to the diff review, which fits the
isolated-worktree model), or answer via `POST /api/runs/{id}/permission`. An in-UI
card surface for plan-node approvals is an open Phase-1 follow-up.

**Review surface (slice 4):** the captain-facing half, following T4's ratified detail
language - the diff opens in an **expanding row** (a plan row's expanded body on the
Tasks screen) and on the **full-width task page** (`TaskWorkspace`); never a right
panel, never a modal. One shared component (`components/tasks/ChangesReview.tsx`)
fetches `GET /api/jobs/{id}/diff` and renders the per-file list (statuses in plain
words) plus the unified change; UI copy is de-jargonized ("isolated copy", "changes" -
git nouns stay in dev docs). Two verdict doors: **Approve & merge changes** invokes the
engine's approve (the slice-2 guarded merge; a conflict surfaces as a plain
needs-attention banner with the server's reason and the job parks in review for a
retry), and **Reject…** demands a one-line reason, then `POST /api/jobs/{id}/reject`
(either engine) marks the job `failed` with `jobs.rejected_reason` recorded and tears
the worktree down unmerged - the project never sees the discarded change. After the
merge the row shows what landed (base branch + merge commit) and keeps the change
readable; the plan canvas header Approve door shows a success notice and keeps a
durable "merged into <branch> · <sha>" line (plus push outcome when applicable) so a
reopened Done plan still says where the work landed. Slice 12's satpam consumes these
same review states.

### Repo-remote connector: BYO push-after-merge (Phase-1 slice 11, T9 - LIVE)

**Why:** the local-first merge is the default posture, but a repo that lives on
GitHub/GitLab/self-hosted eventually needs the merged work on its remote. T9 (captain
ratified) graduates the merge without changing the review model: push happens AFTER
the local approve+merge, and only for code areas that explicitly opted in.
PR-as-review-surface stays out of Phase 1 - the review surface remains singular,
in-app.
**How - BYO to the letter (standing decision #9):** every remote operation shells out
to the host's **own `git`** (`repo_remote.py`; non-interactive - terminal prompts and
ssh asking for input are disabled, so a push that cannot authenticate fails with git's
message instead of hanging). Proxima never brokers auth, stores no tokens, ships no
OAuth flow; if the host's git can push, the connector works. **Per-area opt-in,
auto-offered:** the container settings (Projects screen → Settings) list each code
area with its detected remote (prefer `origin`); an area WITH a remote gets a "push
after merge" toggle (`project_areas.push_on_merge`), **default OFF** - no remote, no
toggle, and nothing is ever pushed without the explicit opt-in. Enabling the toggle
**pins the remote URL** into `project_areas.push_remote_url` (audit F3): the push
refuses if the repo's own `.git/config` - writable by any agent working in the repo -
no longer matches the pinned URL (re-enable the toggle to approve a changed URL).
**Lifecycle:** when a repo job's (or plan's) final approve lands the local merge and
the target area's toggle is on, Proxima runs
`git -c credential.helper= -c core.hooksPath=/dev/null push <remote> <base_branch>`
from the area's repo - the `-c` overrides neutralize agent-planted credential helpers
and pre-push hooks, so nothing in repo config can execute in the API process at push
time (auth stays ambient ssh/BatchMode).
**Failure semantics:** a failed push (diverged remote, auth expiry, network) NEVER
un-merges - the job stays done-and-merged locally; the worktree row records
`push_status='failed'` plus the exact command + git's own output in `push_error`, and
the review surface shows a job-level blocker card with a **Retry push** action.
**GitHub-first, not GitHub-only:** plain `git push` covers any remote; when the remote
URL is GitHub the surfaced info is enriched with the parsed repo web link and whether
the host's `gh` is signed in (informational only - no hard `gh` dependency).
**Endpoints:** `GET /api/projects/{slug}/areas` + `/areas/detect` (each code area now
carries `push_on_merge` + its detected `remote`), `PATCH /api/projects/{slug}/areas/{id}`
(the toggle; enabling requires a detected remote), `POST /api/jobs/{id}/push` (retry,
either engine). Worktree payloads carry `push_status/push_error/push_remote/push_web_url`.

### Long work: timeout auto-continuation (Phase-1 slice 5, T5 - LIVE)

**Why:** A single agent turn is hard-capped by the turn quota. Before slice 5 a job
turn that hit the cap was killed and the job failed (or a goal silently stalled) even
though the work was mid-flight (T5). Long work must survive the per-turn cap.
**How:** when a **job run** (linear step or plan/graph node) hits the quota
(`asyncio.TimeoutError`), the worker salvages the streamed text as before, then
enqueues a **continuation run in the SAME session** - the persistent ACP session
carries the agent's full context - and, for repo jobs, the same worktree (cwd binds to
the job, so file edits persist). The continuation prompt is a **genuine resume**
("inspect the current state of your work and continue from where it stopped"), never a
re-brief. Graph nodes stay `running` and are re-attached to the continuation run
(guarded `running→running` run-id swap), so advancers accept its result as the same
attempt. The chain is capped at **`run_continuation_limit` (config, default 5) per
turn chain**; at the cap the stop is honest and loud: the run/job fails with a
plain-language reason (split the job or raise the quota) and a **plan pauses for
review** - a timed-out job never sits in limbo. Chains are durable on the run rows
(`runs.continued_from_run_id`, `runs.continuation_count`): slice 12's satpam reads a
high continuation count as a confused-agent signal and owns the restart-clean
decision - discarding a worktree is **never** an automatic timeout response.
Chat, goal-mode, collaboration, and review runs keep their pre-slice-5 timeout
behavior unchanged.
**Turn quota (first-class):** `run_timeout_seconds` is an **in-app setting**
(Settings → Agents → Turn quota, stored in `app_settings`, default 900s, bounds
60-7200s). Because it is DB-backed it takes effect on BOTH entrypoints -
`scripts/serve.py` and plain `uvicorn proxima_api.main:app` - and the env overrides
(`PROXIMA_RUN_TIMEOUT_SECONDS`, `PROXIMA_RUN_CONTINUATION_LIMIT`) are now mirrored on
both as fallback defaults. The plan slicer is instructed to size every job to fit ONE
turn quota - continuation is the safety net, not the plan.
**Endpoints:** `GET/PUT /api/settings/runs`.

### Deterministic script steps (Phase-1 slice 6, T6 - LIVE)

**Why:** repeated mechanical work (fetch, convert, check, publish) should not cost an
agent turn every time. A plan step that needs no judgment can be a saved script - fast,
free, and exactly reproducible (T6; ADR-0001's Phase-3 deterministic nodes pulled
forward in minimal form).
**How:** one new node kind, `script`, on the graph engine (not an n8n palette): the
node names a script inside the project container's **`scripts/` folder** plus CLI args,
and executes as a **subprocess** - exec array, never a shell string - with the
container root as cwd and a minimal environment (no server env). I/O contract: args
(`{{var}}` fills from the workflow input) + one JSON object on stdin
(`{"job_input": …, "upstream": […]}` - the graph engine's existing typed hand-off);
stdout is the node output, validated against the node's `output_kind`/`output_schema`
like any agent node; exit code decides success/failure. Script runs queue through the
ordinary runs table (`kind='wf_script_node'`), so they share the dispatch budget, turn
quota, heartbeats, and crash reaping - but never touch a runner/ACP, and never
auto-continue.
**Trust = content-hash binding (captain's decision):** a script's first run - or any
run after its bytes changed - blocks with a one-time approval ask (the plan pauses in
review; the node inspector shows **Approve script & run**). The approval card renders
the script's **actual content + sha256** (fetched together from
`GET .../nodes/{node_id}/script`), and the approve request echoes that hash back -
the server refuses with 409 if the file changed after review, so the owner approves
bytes, never a filename (audit F4). Approving records the sha256 in `script_trust`;
unchanged trusted scripts then run with **no per-run approval** - that is the whole
deterministic + free payoff. At run time the hashed bytes execute from a **private
temp copy** taken at hash time, so a concurrent edit of the project file after the
trust check cannot change what runs. Approvals and blocks are visible in the step's
timeline (`script.approval.required`, `script.trust.approved`) and the audit log.
**Reuse awareness:** agents write and maintain the scripts as ordinary job output,
each starting with a header comment block (`# Description:` / `# Inputs:` /
`# Outputs:`). Proxima auto-scans `scripts/` into a catalog (name + one-line
description) injected into every project run preamble alongside the wiki catalog,
with the instruction to prefer reusing/extending an existing script. The plan slicer
is given the same catalog and may emit script jobs - but only for scripts that exist.
**UI:** script nodes render distinctly (dotted outline, `⚡ scripts/<command>` in the
mono face, last output line on the card and Tasks list row); the canvas has a
**+ Script** tool and the inspector edits command/args/contract/gate.
**Endpoints:** `GET /api/graph/jobs/{id}/nodes/{node_id}/script` (content + sha256
for the approval card), `POST /api/graph/jobs/{id}/nodes/{node_id}/approve-script`
(body carries `expected_sha256`; 409 on mismatch).

### Satpam supervision loop (Phase-1 slice 12, T10 - LIVE)

**Why:** the heartbeat/reaper catches DEAD runs; nothing caught an agent that is alive
but unproductive - burning continuation turns while producing no repo change, repeating
itself, or guessing at a decision that belongs to the owner (T10; firstmate/watcher
supervision patterns ported to Proxima's primitives, captain ratified).
**How - ONE fleet-level loop** (`satpam.py`, a sibling of `RunReaper` on the worker
loop's cadence, self-paced by its Settings interval): each sweep evaluates every
running job's active **continuation chain** (slice 5's `continuation_count` is the
turn boundary - one evaluation per continuation turn, not per token) against DURABLE
signals only - DB rows and worktree diff signatures, never the agent's stream, and
never an LLM call. Fail-quiet by contract: a supervision error is logged and swallowed,
never crashing the worker or a run.
**Detection ladder:** *dead* - heartbeat/reaper, unchanged (the satpam consumes its
outcome); *stalled* - a repo chain whose continuation turns leave the worktree
signature (`worktrees.work_signature`: branch head + uncommitted status + tracked diff
+ untracked stats) unchanged N turns in a row; *looping* - consecutive turns whose
salvaged output + repo state hash identically N turns in a row; *confused* - the
continuation cap (slice 5) or repeated output-contract validation failure
(`node_states.contract_failures`, second strike escalates).
**Action ladder (automation line, captain ratified):** (a) **steer** - one corrective
prompt into the job's next continuation turn (amended in place on the queued run, or
held in `satpam_watch.steer_pending` for the next one): AUTOMATIC, logged; (b)
**restart-clean** - cancel the stuck attempt and re-run fresh (a new node session for
plans; step one with fresh context for classic tasks): AUTOMATIC only for NON-repo
work; for repo work it is a **pending approval card** in Tasks (restart discards the
worktree - destructive, owner-gated; approve re-cuts fresh from the repo's current
HEAD and re-runs the plan's repo slice); (c) **pause + escalate** - the chain is
cancelled and the job parks in review with a plain-language what-happened record.
Steer→restart→escalate is per stuck episode; a restart that did not help escalates
rather than thrashing.
**Decision-hold (T10 #4):** the node prompt defines a structured output-contract
marker - a reply starting **`DECISION_NEEDED: <question>`** - for a genuine open owner
decision. The node parks in the existing `review` state with the question on
`node_states.question`; its dependents hold on their own (their dependency never
reaches `done`) while **independent DAG branches keep dispatching**; only when they
drain does the plan park. Answering (`…/answer`, usable while the plan RUNS) stores
`node_states.answer` and re-runs the node with the decision in its prompt.
**No silent interventions (T10 #5):** every action is a `satpam_interventions` row
(shown as the task's "Watchdog log" in TaskWorkspace and on the Recipes canvas, with
the pending-restart approval card on top) plus a `satpam.*` timeline event
(`satpam.steered` / `satpam.restart.queued` / `satpam.restarted` / `satpam.escalated`).
Thresholds are a Settings panel (Agents → Watchdog): N no-progress turns (default 2)
and the sweep cadence (default 60s), bounds-checked in `app_settings`.
**Endpoints:** `GET/PUT /api/settings/satpam`,
`POST /api/jobs/{id}/satpam/{intervention_id}/approve|dismiss`,
`POST /api/graph/jobs/{id}/nodes/{node_id}/answer`.

## 9. Schedules (cron)

**Why:** Recurring agents — daily report, watch-and-summarize — while you sleep.
**How:** `schedules` table + a 60s scheduler loop that materializes only *due* jobs
(own 5-field cron matcher; overlap policy skip/allow). Failed step fails the job.
**Run now** fires a schedule on demand and opens the task it spawned, so the owner can
prove a schedule before leaving it to fire unattended. It reuses the tick's own
`_spawn_scheduled_job`, so it exercises the real cron target (workflow, project,
profile, stored input, and repo worktree binding) instead of a lookalike; it passes no
minute key, so a manual run cannot claim — and thereby swallow — the scheduler's slot
for that minute. It works on a disabled schedule (`enabled` gates the tick, and trying a
schedule out is exactly when it is still off) and reports an overlap skip as a 409 rather
than silently no-op'ing.
**Endpoints:** `POST/GET/PATCH/DELETE /api/schedules[...]`, `POST /api/schedules/{id}/run`.

## 10. Projects (workspaces)

**Why:** Scope agents to a folder — your real code, not a sandbox.
**How:** `projects` table. Create a scaffolded project OR **link an existing folder**
(`/api/projects/link`, jailed to configured link roots). Chat/terminal/files all
operate on the project path. The screen is a card grid (one card per project: select,
Rename, remove), with both ways in behind one **Add project** modal — a project holds a
name and a slug, which does not earn a detail panel. Removal distinguishes what the API
actually does: a linked folder is unlinked and its real files stay; a Proxima-created
project is deleted from disk. On **first run**, right after setting a password, an
onboarding step (`screens/WorkspaceOnboarding.tsx`, reusing the `FolderLinker`
browser) offers to link a real code folder before landing in the app; skipping
uses the starter project auto-provisioned under the data dir.
**Endpoints:** `GET/POST /api/projects`, `/projects/link`, `GET /api/fs/dirs`,
`PATCH/DELETE /api/projects/{slug}`.

### Work-container areas (Phase-1 slice 1 - data layer only)

**Why:** A project is a *work container*, not "a repo": it holds zero-or-more **code
areas** (subfolders that are git repos; `.` = repo at root) plus one **ops area** (the
non-code output space - the conventional `artifacts/ reports/ exports/ wiki/` subdirs
belong to it). Slice 2's worktree machinery (above, flag-gated) cuts its safe copy from
a job's target code area; the slicer that binds each job to exactly one area at slice
time is slice 3 (T1's explicit job→target decision).
**How:** `project_areas` table (row per area; `source` = `auto`/`manual`/`excluded`).
Identification is **hybrid** (T1): `project_areas.py` auto-detects `.git` subfolders
(bounded depth, skips node_modules/.venv/dist-style dirs, never descends into a
detected repo) at project create/link and on demand; the owner can manually add,
correct, or remove areas. Manual rows are never clobbered by re-detection, and removal
leaves an `excluded` tombstone so re-detection cannot resurrect the area. Existing
projects were wrapped in place by migration 18: root itself a repo → sole code area
`.`; no repo → zero code areas; no file moves. Artifact scanning still ignores areas;
execution reads them only through slice 2's flag-gated repo-job path (`jobs.target_area_id`
→ worktree cwd). Project payloads include `code_areas` + `ops_area`.
**Endpoints:** `GET/POST /api/projects/{slug}/areas`,
`DELETE /api/projects/{slug}/areas/{area_id}`, `POST /api/projects/{slug}/areas/detect`.

## 11. Files & uploads (APIs)

**Why:** Read/write project files safely from every surface that needs them.
**How:** Tree + file read/write (CodeMirror editor), HTML/MD preview, mkdir/rename/delete,
chunk-streamed file upload with collision-safe naming and a configurable 100 MB default
limit, plus an authenticated raw/preview
route (for images and embedded previews). A separate bounded, path-only reference index
powers `@` autocomplete without returning file contents; produced artifacts from the
project artifact scan are merged into the same picker on the client.
These APIs power the **Files tool** on the right rail (the project tree + inline
editor as an overlay panel, any context), the **Archive**'s record viewer
view, the **Wiki** tree under Settings → Knowledge & Wiki, chat attachments, and `@`
file/artifact references — with the in-browser **Terminal** as the raw escape hatch.
**Endpoints:** `/api/projects/{slug}/tree`, `/file`, `/upload`, `/fs/*`, `/raw`,
`/reference-files`, `/artifacts`, `/api/preview/{slug}/{path}`.

## 12. Run & Preview app

**Why:** Launch a project's dev server and preview it in-app — from the **Preview**
tool on the right rail, from an app-type artifact, or from the recipe test bench.
**How:** `AppManager` runs one owner-confirmed dev process per project with a filtered
environment. The preview must be served root-relative on its own origin (SPA HTML uses
absolute asset paths and HMR opens a WebSocket to the page origin), so each vantage gets
one: local direct preview uses the other loopback hostname so the Proxima cookie is not
sent across ports; remote preview uses the app's **preview relay port** on the Proxima
host (reported as `preview_port` in app status; bind interface via
`PROXIMA_PREVIEW_BIND`, default `auto` = the tailnet interface or loopback, never
`0.0.0.0`; `off` disables) or, with an apps domain configured, the
`preview-<slug>.<apps_domain>` subdomain. Relay and subdomain proxy share one engine:
HTTP + WebSocket forwarding, Host rewritten to the local dev port (Vite-style
allowed-host checks pass), gated by a short-lived preview-only cookie — never the owner
API token — and they strip cookies/auth before forwarding and strip upstream
`Set-Cookie`. Same-origin fallback and generated HTML use an opaque iframe sandbox.
This is credential-leak mitigation, not OS/container isolation; the command still runs
as the Proxima service user. The relay only guards its own port: detected-app
suggestions bind `127.0.0.1`, `HOST=127.0.0.1` is defaulted into the dev-server env,
and app status reports `broad_bind` (surfaced as a UI warning) when the dev server is
found listening beyond loopback - that port is LAN/tailnet-reachable with no auth.
When a command self-exits (short script, crash, or non-server entry point), status keeps
a sticky `exited` + `exit_code` payload across polls so Run & Preview can show Finished
vs Failed with the log and a next-step hint instead of a silent bare dump.
**Endpoints:** `/api/projects/{slug}/app/start|stop|status`, `/apps`.

## 13. Image generation and Design Studio

**Active:** image generation remains available through `/image` (alias `/gambar`).
It uses the image provider selected in Settings, saves output under
`artifacts/media/images/`, and returns the artifact in the originating chat. Images
attached to the message or explicitly selected through `@` (rendered as
`![name](path)` markdown by the composer) are used
as reference/source images when the selected provider advertises `imageEdit` — the
first attachment is the primary source and the rest are passed as `extra_images` when
the provider also supports `referenceImages`; the reference markdown is stripped from
the prompt so the model gets clean instructions. If the provider is text-to-image only,
the attachments are ignored and the reply says so. Existing image and media files remain
readable through the normal artifact/file surfaces.

**Clarify-on-thin-brief:** when a `/image` or `/design` command carries almost no
direction (no attached image and fewer than 3 words after the command), the backend does
NOT generate/draft something generic — it replies in the same chat with a compact
`<question-form>` (image: subject/style/aspect; design: goal/format/audience/mood/copy).
The form carries a `submit-as` attribute, so answering re-issues the original command with
the answers as an enriched brief, and the same media path runs again — now with enough to
act on. A brief that already has ≥3 words (or an attached image) skips the form and runs
immediately. Implemented in `routes/chat.py` (`_media_brief_is_thin`, `_MEDIA_BRIEF_FORMS`,
`_complete_media_ask`); the frontend prepends `submit-as` on submit
(`questionForm.ts` / `QuestionForm.tsx`).

These synchronous media completions (the clarify form and design draft cards)
finish inside the POST and emit their run events before the client can subscribe to the
stream, so `ChatScreen` treats a `status: "completed"` create-run response specially: it
loads the assistant reply directly instead of waiting on the stream — otherwise the
composer would sit stuck on the "Simmering…" thinking indicator.

**Design Studio (active, server-gated):** an AI-assisted canvas where the agent
drafts **editable layered scenes** (text stays real text) and the human refines them
directly. The Design home takes a brief (Graphic / Slide deck / Mobile app / Website)
or a size template (Instagram post/story/carousel, X post, poster, …) and opens a
linked **design session**: the agent replies with a `<design-scene>` block the Konva
canvas applies live. The studio offers select/move/resize with a full inspector
(text, fonts, fills/gradients, artboard presets), Layers/Assets panels, a
selection-aware chat, undo/redo + version history, multi-image reference inputs, an
eyedropper, a per-project brand guide (`design.md`, generatable from reference
URLs/images), and Export (PNG/JPG/PDF/HTML). Scenes persist at
`artifacts/design/<id>/scene.json` and appear as design records in the Archive.
See [DESIGN-STUDIO.md](DESIGN-STUDIO.md) for the full contract.

The server-owned flag `PROXIMA_FEATURE_DESIGN_STUDIO` gates it: on by default,
with `proxima.env` as the owner opt-out (read at boot).
`GET /api/config` publishes the effective flag. When disabled, the frontend omits its
navigation, deep links, commands, settings, provider health checks, artifact bridge
actions, and agent guidance, and backend guards return HTTP 503 with the
`feature_disabled` payload before message creation, database writes, provider calls,
file writes, subprocesses, or collaboration dispatch.
Video Studio, editable video projects, and the `/video` generation surface were removed;
ordinary video files remain readable and playable as generic artifacts.

## 14. Archive: durable deliverable registry (Phase-1 slice 8, T4 - LIVE)

**Why:** deliverables used to exist only as a capped (~40 item) mtime scan - no
memory, no approval state, no trace of which job produced a file. The Archive is now
a **registry, not a scanner**: the scanner discovers files, the registry remembers
them as durable records that survive file moves and deletion (a missing file flips
`file_missing` on the record; the record stays).

**How:** every finished run (agent or script) feeds its scanned output links into
`artifact_records` (module `artifact_registry.py`): one row per deliverable
**version** with name, type (scanner types + `script-output` for generic files a
script step produced), project + path, size, produced date, lineage
(session → job/node → run), approval status, and a version chain. Identity is
(project, type, path): a new producer at the same identity creates v(n+1) and
automatically marks prior versions superseded; re-scans by the same run (or later
steps of the same still-draft job) refresh in place, so feeding is idempotent.
Migration 23 seeds the registry from the current scanner output so existing
projects' artifacts appear as draft records on upgrade.

**Approval is ONE status with two doors (synced):** `draft / review / approved /
superseded`. Approving a job in its Tasks review auto-approves the records that job
produced; the Archive page edits the SAME field for the late/batch/supersede cases.
Never two separate approval states.

**Registry queries replace the item cap:** paginated newest-first list with
project/type/status/date filters, text search, and facet counts; each record has a
permanent per-project address (`/api/archive/{project}/{slug}` — the UI's
`#archive/<project>/<slug>` permalink) with same-path version history plus
project-level newer/older record navigation (by produce date, not version).
Inline and full-record previews cover docs (markdown), pages (HTML iframe),
images/video, and **designs** (first artboard via the same `MiniPreview` thumb as
Design gallery, loaded from `scene.json`). Apps and other types still point at Open.
Chat result cards and the iterate Result view keep using the live scan
(`GET /api/projects/{slug}/artifacts`, unchanged).

**Endpoints:** `GET /api/archive`, `GET /api/archive/{slug}/{record_slug}`,
`POST /api/archive/records/{record_id}/status`.

## 15. Wiki + memory (knowledge)

**Why:** Per-project + global knowledge that compounds across sessions.
**How:** Markdown files under each project's `wiki/`; a built index + tree; global
aggregation. Fed by Chat→Wiki (§5). Opened from **Settings → Knowledge & Wiki**
(Files / Graph / Search). Preview renders `[[wikilinks]]`; existing targets open
the note, and **missing (red) links create the note on click** (title heading stub,
open in edit beside the current note when nested) so owners are never stuck on a
dead link.
**Endpoints:** `/api/projects/{slug}/wiki/all`, `/api/wiki/all`, `/tree`, `/file`, `/fs/*`.

## 16. Terminal

**Why:** A real shell in the cockpit, scoped to the project.
**How:** `terminal.py` over `WS /api/ws/terminal`. Opened from the **Terminal** tool
on the right rail; once opened it stays mounted so shells survive closing the panel.

## 17. Command palette (quick commands)

**Why:** A catalog of quick slash-style commands runnable from chat.
**How:** `commands.py` — `command_catalog()` lists them; `execute_command()` runs one.
**Endpoints:** `GET /api/commands/catalog`, `POST /api/commands/execute`.

> Note: an earlier *advisory command-policy classifier* (`POST /api/policy/command/check`)
> was **removed** — it never gated real agent/tool execution (the agent runs its own
> shell inside the runner CLI, not through this API), so it gave a false impression of a
> guard. The real access boundary is network reachability (single-user). See
> [security-boundaries.md](security-boundaries.md).

## 18. New task launcher + search

**Why:** Delegate a one-off task, and see what needs you. The app itself lands on
**Chat** — the launcher opens from the Tasks screen's `+ New task`.
**How:** The launcher is deliberately minimal — a greeting, the **Task Composer**
(project + agent + Guarded/Autonomous policy), and an **attention strip** when
jobs are waiting in review (jump to the first, or open Tasks). It polls
`GET /api/dashboard` every 5s; the dashboard payload also carries `authHealth` —
cached background checks (`auth_health.py`, 60s TTL, never on the request path)
of the selected image provider plus every runner referenced by a profile —
though the current Home renders only the review-attention data. Global **Search**
(magnifier in the top bar) covers chats, messages, projects, and designs.
**Endpoints:** `GET /api/dashboard`, `/api/runs/active`, `GET /api/search`.

## 19. Audit log

**Why:** An activity trail of meaningful actions.
**Endpoints:** `GET /api/audit`. (Roles/users management removed in single-user.)

## 20. Reliability (cross-cutting)

Heartbeat/reaper for hung runs, the satpam supervision loop for alive-but-unproductive
ones (see §8 Satpam), per-session serialization, graceful shutdown, output
salvage, orphaned-run cleanup, a per-turn quota (`run_timeout_seconds` — an in-app
setting, default 900s; see §8 Long work) with cancel-on-timeout plus capped
auto-continuation for job runs, and daily DB backup (`proxima-backup` timer with
`VACUUM INTO`). Setup failures are finalized immediately instead of waiting for the
reaper. Run completion is status-guarded: cancellation cannot be overwritten by a late
media result, message-review result, collaboration synthesis, draft, or graph update.

## 21. Updates (version check + self-update)

**Why:** Every install should be one click away from the latest release without
the owner babysitting `git pull`.
**How:** `UpdateManager` polls GitHub Releases every 6h (silent failure — an
offline host, private repo, or GitHub hiccup never surfaces to the user); the
owner can also trigger a manual check from a **Check for updates** button in
Settings → Updates. When a newer release exists, the sidebar shows an update
pill that opens a release-notes modal (rendered markdown) with a one-click
**Update now**, which requires a clean checkout and runs `scripts/proxima update`
(`git pull --ff-only` + locked build/tests + restart + health check, tracked via an
`update-status.json` marker file) behind a
blocking overlay that polls `/api/health` until the new version answers, then
reloads. A build failure happens before restart, while a failed post-restart health
check is surfaced for manual log inspection (there is no automatic checkout/DB rollback).
**Windows:** check/notify
works the same; one-click apply is unsupported and returns a manual `git pull`
command instead.
**Endpoints:** `GET /api/update/status`, `POST /api/update/check`,
`POST /api/update/apply`.

---

## Removed (was multi-user, now single-user)

In-app user accounts, roles (`environment_admin`/`member`), multi-user login,
team bootstrap, invite links, project membership/sharing, project
visibility (private/shared), team name. Collaboration model is instead: **everyone
self-hosts their own instance + shares folders/repos.** The runtime model is one
owner with one password/session gate; legacy invite/member tables have been dropped.

## Single-workspace shell ("Deck", T3)

+ **One workspace, no Ops/Code switch.** The left nav is flow-ordered — Chat, Tasks, Recipes, Projects, Archive, gated Design — with New chat on top and project-scoped recent chats beneath. Chat is the default landing view. Agents and Settings live in the profile menu; Wiki lives under Settings → Knowledge & Wiki. Server feature flags remain authoritative.
+ **Chat** is the front door: brainstorm, then **Slice into plan** promotes the conversation into a runnable plan. The chat header carries the real context (session, project, agent) and its **New chat** action clears the active session; the chat remains lazily created on first send.
+ **Tasks** is the permanent execution/review index; its `+ New task` button opens the launcher — a single integrated Task Composer with searchable Project/folder context, selected Agent, a combined Add menu for attachments/image/design, and Guarded or Autonomous execution policy. It creates a durable ad-hoc job and opens a dedicated hash-addressable task workspace with live progress, review, approval, and deliverables. The linked execution session is not a visible chat conversation.
+ The single **Recipes** destination contains the plan Editor (graph canvas) and Scheduled automation. The graph is enabled by default; its flag is a recovery switch rather than a hidden experimental mode. Scheduled is an internal mode rather than a duplicate sidebar route or database concept; it keeps five-field cron, overlap, enabled, and delete behavior.
+ **Right tool rail** (`ToolDock`): Terminal, Files, and Preview open as overlay panels above the current screen, project-scoped, in any context; the rail's gear opens Settings and Escape closes the panel. Terminal and Files stay mounted after first open (shells and unsaved edits survive a closed panel); Preview unmounts because its dev server is a backend process. The Archive remains the destination for agent outputs; Design remains a separate feature-gated canvas, with artifact source fallback when disabled.
+ **De-jargon rule:** primary surfaces say "agent" and "tools" — never "runner", "MCP", "profile", env-var names, or raw stack traces. That detail lives in Settings → Agents and the docs.

Authentication remains single-owner defense in depth: first run sets a password, later requests require a bearer token or `proxima_session` HttpOnly cookie, login establishes the session, and resume restores it.
