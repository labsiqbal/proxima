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

**Why:** Proxima drives the AI coding agents you already own (Claude Code, Codex,
Hermes, and Pi) over ACP — no baked-in model.
**How:** `runner_specs.py` defines each runner's spawn argv + credential home. The
worker (`worker.py`) starts one ACP subprocess per (runner, home, cwd) on demand;
`runner_spec(run.runner_id)` makes it runner-agnostic. Default resolves via
`default_runner()` (env → first *ready* runner → fallback).
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
`GET /api/runners/{id}/capabilities`.

## 3. Chat (the core loop)

**Why:** Talk to an agent; it streams back, runs tools, asks for approvals.
**How:** A `session` holds messages; a `run` is one agent turn driving the ACP
session. The worker is bounded-concurrent (`run_worker_concurrency`) so one slow run
never blocks other chats. Tool permissions ask the owner by default; auto-approve is
an explicit Settings opt-in. Streaming uses SSE (`/events/stream`) + WebSocket.
**Endpoints:** `POST /api/chat/send`, `/api/sessions/{id}/runs`, `/messages`,
`/events/stream`, `WS /api/ws/sessions/{id}`, `/runs/{id}/cancel`, `/runs/{id}/permission`.

### Project-file references (`@`)

Every project-scoped prompt surface shares the same file picker: Code chat, the Ops
Task Composer, workflow authoring chat and graph fields, Design Home, Design chat, and
Graph Home. Typing `@` filters a path-only project index and inserts the selected
reference at the caret; arrow keys plus Enter/Tab work as well as the mouse. Ordinary
files become project-relative paths, which project-scoped runners can open from their
working directory. Images become `![name](path)` references, so `/image` providers and
design-agent vision receive the selected pixels instead of only a filename. The popup
viewport shows four ranked matches at once; additional matches remain available by
scrolling, while typing more of the path narrows the full bounded index.

The picker never expands text-file contents into the prompt. Its authenticated
`GET /api/projects/{slug}/reference-files` index is bounded and project-jailed, skips
symlinks, dependency/build/cache and hidden directories, and omits common secret/key
files. Vision loading re-validates paths against the session project and accepts only
bounded image files (10 files, 8 MB each, 32 MB total).

### Per-prompt Brainstorm / Debate modes

> **Status:** the `Brainstorm`/`Debate` chips are shown only in **Code chat**. Ops
> Task Composer omits collaboration modes. The retained Studio composer also disables them, but Design Studio itself
> is unavailable while `PROXIMA_FEATURE_DESIGN_STUDIO=0`.

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
**Endpoints:** `POST /api/sessions/{id}/goal`, `/goal/cancel`.

## 5. Chat → Wiki (knowledge continuity)

**Why:** Distill a conversation into a durable wiki note.
**How:** `wiki-note/draft` spawns a run that produces a `wiki.draft` event → preview
→ `wiki-note/commit` writes the markdown + rebuilds the wiki index.
**Endpoints:** `POST /api/sessions/{id}/wiki-note/draft`, `/wiki-note/commit`.

## 6. Chat → Workflow (Convert to Workflow)

**Why:** Turn a proven conversation into a reusable recipe.
**How:** `promote-workflow` has an architect agent decompose the chat. The graph path
is enabled by default and emits a
normalized `{nodes,edges}` DAG with typed outputs and review gates; the user must review
or edit the queued frozen plan before explicitly starting it. The feature flag remains
an owner recovery switch; the legacy ordered-step path is retained only for existing data.
**Endpoints:** `POST /api/sessions/{id}/promote-workflow`.

## 7. Workflows (graphs) + schedules

**Why:** Codify a repeatable multi-step process the agent can execute — with branches,
per-node agents and review gates, not just a straight line.
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
`engine='graph'` job a manual create + start produces. With the graph feature flag off,
a graph schedule is skipped with a logged warning rather than left as a job nothing will
advance.
**Endpoints:** graph routes (§Graph workflow engine), `GET/POST /api/schedules`,
`POST /api/schedules/{id}/run`; legacy linear rows keep `GET/PATCH /api/workflows/{id}`.

## 8. Tasks / jobs (executions)

**Why:** Every execution — a workflow run or an ad-hoc 1-step task — as one trackable
pipeline.
**How:** classic `engine='linear'` jobs use a frozen step snapshot and run
sequentially in one ACP session (context carries free). Gated graph jobs share the
job lifecycle but keep per-node state in `node_states` and are intentionally excluded
from the linear Tasks list. Live-polls while running; auto-archive after 30 days.
Old kanban tasks were migrated to 1-step jobs.
**Endpoints:** `POST /api/jobs`, `/jobs/{id}/start`, `/jobs/{id}/link-run`, `/approve`, `GET /api/jobs[...]`.

## 9. Schedules (cron)

**Why:** Recurring agents — daily report, watch-and-summarize — while you sleep.
**How:** `schedules` table + a 60s scheduler loop that materializes only *due* jobs
(own 5-field cron matcher; overlap policy skip/allow). Failed step fails the job.
**Run now** fires a schedule on demand and opens the task it spawned, so the owner can
prove a schedule before leaving it to fire unattended. It reuses the tick's own
`_spawn_scheduled_job`, so it exercises the real cron target (workflow, project,
profile, stored input) instead of a lookalike; it passes no minute key, so a manual run
cannot claim — and thereby swallow — the scheduler's slot for that minute. It works on a
disabled schedule (`enabled` gates the tick, and trying a schedule out is exactly when it
is still off) and reports an overlap skip as a 409 rather than silently no-op'ing.
**Endpoints:** `POST/GET/PATCH/DELETE /api/schedules[...]`, `POST /api/schedules/{id}/run`.

## 10. Tasks (kanban)

**Why:** Steerable per-task agent threads on a board (todo/doing/review/done).
**How:** `tasks` table; auto status doing→review, human marks Done. Each task has a
backing session/thread. (Unified under the jobs model.)
**Endpoints:** `GET/POST /api/projects/{slug}/tasks`, `PATCH/DELETE /api/tasks/{id}`.

## 11. Projects (workspaces)

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

## 12. Files workspace

**Why:** Browse/edit the whole project tree with live preview.
**How:** Tree + file read/write (CodeMirror), HTML/MD live preview, mkdir/rename/delete,
chunk-streamed file upload with collision-safe naming and a configurable 100 MB default
limit, plus an authenticated raw/preview
route (for images and embedded previews). A separate bounded, path-only reference index
powers `@` autocomplete without returning file contents.
**Endpoints:** `/api/projects/{slug}/tree`, `/file`, `/upload`, `/fs/*`, `/raw`,
`/reference-files`, `/api/preview/{slug}/{path}`.

## 13. Run & Preview app

**Why:** Launch a project's dev server and preview it in-app.
**How:** `AppManager` runs one owner-confirmed dev process per project with a filtered
environment; an authenticated reverse proxy serves it. Local direct preview uses the
other loopback hostname so the Proxima cookie is not sent across ports. Remote preview
uses a short-lived preview-only cookie, never the owner API token. Both proxies strip
cookies/auth before forwarding and strip upstream `Set-Cookie`. Same-origin fallback
and generated HTML use an opaque iframe sandbox. This is credential-leak mitigation,
not OS/container isolation; the command still runs as the Proxima service user.
**Endpoints:** `/api/projects/{slug}/app/start|stop|status`, `/apps`.

## 14. Image generation and Design Studio

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

**Temporarily disabled by default:** Design Studio remains in source and is
server-gated with `PROXIMA_FEATURE_DESIGN_STUDIO=0`. `GET /api/config` publishes the effective flag.
When disabled, the frontend omits their navigation, deep links, commands, settings,
provider health checks, artifact bridge actions, and agent guidance. Backend guards
return HTTP 503 with the `feature_disabled` payload before message creation, database
writes, provider calls, file writes, subprocesses, or collaboration dispatch.

The retained Studio implementation includes layered Konva scenes and exports. It is
not an advertised or reachable capability in the default Proxima release. Image
generation does not expose *Edit in Design Studio* while that feature is disabled.
When enabled, both Design Home and its chat share the project-file picker; selected
images are appended to the design run's jailed vision inputs.
Video Studio, editable video projects, and the `/video` generation surface were removed;
ordinary video files remain readable and playable as generic artifacts.

## 15. Wiki + memory (knowledge)

**Why:** Per-project + global knowledge that compounds across sessions.
**How:** Markdown files under each project's `wiki/`; a built index + tree; global
aggregation. Fed by Chat→Wiki (§5).
**Endpoints:** `/api/projects/{slug}/wiki/all`, `/api/wiki/all`, `/tree`, `/file`, `/fs/*`.

## 16. Terminal

**Why:** A real shell in the cockpit, scoped to the project.
**How:** `terminal.py` over `WS /api/ws/terminal`.

## 17. Command palette (quick commands)

**Why:** A catalog of quick slash-style commands runnable from chat.
**How:** `commands.py` — `command_catalog()` lists them; `execute_command()` runs one.
**Endpoints:** `GET /api/commands/catalog`, `POST /api/commands/execute`.

> Note: an earlier *advisory command-policy classifier* (`POST /api/policy/command/check`)
> was **removed** — it never gated real agent/tool execution (the agent runs its own
> shell inside the runner CLI, not through this API), so it gave a false impression of a
> guard. The real access boundary is network reachability (single-user). See
> [security-boundaries.md](security-boundaries.md).

## 18. Home dashboard + search

**Why:** The cockpit's cockpit — pulse of runs/tasks/projects; jump anywhere.
**Endpoints:** `GET /api/dashboard`, `/api/runs/active`, `GET /api/search`.
**Connections card (auth health):** `/api/dashboard` includes `authHealth` — cached
background checks (`auth_health.py`, 60s TTL, never on the request path) of the
selected image provider plus every runner referenced by a profile (deep auth check
for hermes/codex). Disabled Studio providers are omitted. Home shows a compact
"Connections" card beside System readout: green/red dot per check, with the
actionable fix detail on failures and a jump to Settings. Saving active provider
settings calls `auth_health.invalidate()` so the card re-checks on the next 5s poll.

## 19. Audit log

**Why:** An activity trail of meaningful actions.
**Endpoints:** `GET /api/audit`. (Roles/users management removed in single-user.)

## 20. Reliability (cross-cutting)

Heartbeat/reaper for hung runs, per-session serialization, graceful shutdown, output
salvage, orphaned-run cleanup, run timeout (configurable `run_timeout_seconds`, default
900s) + cancel-on-timeout, and daily DB backup (`proxima-backup` timer with
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

## Compact shell, Ops tasks, and Code

+ **Ops** uses a single integrated Task Composer with searchable Project/folder context, selected Agent, a combined Add menu for attachments/image/design, and Guarded or Autonomous execution policy. Home does not duplicate Tasks, Scheduled, Artifacts, or Projects as dashboard cards. It creates a durable ad-hoc job and opens a dedicated hash-addressable task workspace with live progress, review, approval, and deliverables. The linked execution session is not a visible Code conversation.
+ **Code** opens the current chat and adds only a real-context header (session, project, profile). Only the Code header’s **New session** action clears the active session; the chat remains lazily created on first send.
+ The sidebar adapts by workspace. Ops contains New task, Tasks, Projects, Workflows, Artifacts, and gated Design. Code contains New session, Projects, Terminal, and project-scoped recents. Tasks is the permanent execution/review index; Ops Home and workflow runs open the same task workspace. Agents and Settings live in the profile menu; Wiki lives under Settings → Knowledge & Wiki. Server feature flags remain authoritative.
+ The single **Workflows** destination contains the graph Editor and Scheduled automation. The graph is enabled by default; its flag is a recovery switch rather than a hidden experimental mode. Scheduled is an internal mode rather than a duplicate sidebar route or database concept; it keeps five-field cron, overlap, enabled, and delete behavior.
+ Terminal is lazy-mounted on first visit and then hidden rather than unmounted, preserving PTYs. Artifacts remains the destination for agent outputs; Design remains a separate feature-gated canvas, with artifact source fallback when disabled.

Authentication remains single-owner defense in depth: first run sets a password, later requests require a bearer token or `proxima_session` HttpOnly cookie, login establishes the session, and resume restores it.
