# Proxima — Capability Map

What's built, why it exists, and how it works. A reference for understanding what
this cockpit is actually capable of. (Derived from the code, not aspirational.)

> **Where do I edit when I add/change a feature?** See the companion
> [reference/feature-map.md](reference/feature-map.md) — a per-feature grid of
> code locations (backend + frontend), tables/events touched, relations, and
> status/flag. This doc explains *what & why*; that one maps *where*.

> **Model:** single-user cockpit. One owner, no in-app accounts. The access gate is
> the network (loopback / Cloudflare Access). The owner is auto-created on first
> request; the frontend auto-logs-in via `POST /auth/auto` (no password). Per-user
> data lives outside the repo (`~/.local/share/proxima/`).

---

## 1. Agents & runners (bring-your-own-agent)

**Why:** Proxima drives the AI coding agents you already own (Claude Code, Codex,
Hermes, Gemini, …) over ACP — no baked-in model.
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
filtered to the selection, re-applied idempotently before each run so newly installed
host skills appear automatically. Not applicable to `claude_live_home` profiles (they
already use the host config directly).
**Endpoints:** `GET/POST /api/profiles`, `PATCH/DELETE /api/profiles/{id}`,
`GET /api/runners/{id}/capabilities`.

## 3. Chat (the core loop)

**Why:** Talk to an agent; it streams back, runs tools, asks for approvals.
**How:** A `session` holds messages; a `run` is one agent turn driving the ACP
session. The worker is bounded-concurrent (`max_concurrent_runs`) so one slow run
never blocks other chats. Streaming via SSE (`/events/stream`) + WebSocket.
**Endpoints:** `POST /api/chat/send`, `/api/sessions/{id}/runs`, `/messages`,
`/events/stream`, `WS /api/ws/sessions/{id}`, `/runs/{id}/cancel`, `/runs/{id}/permission`.

### Per-prompt Brainstorm / Debate modes

> **Status:** the `Brainstorm`/`Debate` chips are shown in the **main chat and task
> chat**. The retained Studio composer also disables them, but Design Studio itself
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
**How:** `promote-workflow` has an architect agent decompose the chat into ordered
steps (name + instruction + expected output).
**Endpoints:** `POST /api/sessions/{id}/promote-workflow`.

## 7. Workflows (reusable recipes) + Iterate

**Why:** Codify a repeatable multi-step process; refine it live on a stage.
**How:** `workflows` table stores steps as a JSON array (edited/snapshotted as one
unit) + typed `{{inputs}}`. The **Iterate** stage ("Panggung") runs a dry-run and
shows a universal Result view (designs / live apps / articles / files); "Save to
workflow" folds the conversation back into the recipe. Per-step **rules** (injected)

+ **skills** hints + mid-workflow **review gates** (pause → approve / edit-&-continue).
**Endpoints:** `POST/GET/PATCH/DELETE /api/workflows[...]`, `/workflows/{id}/iterate`.

## 8. Jobs / Activity (executions)

**Why:** Every execution — a workflow run or an ad-hoc 1-step task — as one trackable
pipeline.
**How:** `jobs` table = frozen snapshot of steps + per-step state. Steps run
sequentially in one ACP session (context carries free). Live-polls while running;
auto-archive after 30 days. Old kanban tasks were migrated to 1-step jobs.
**Endpoints:** `POST /api/jobs`, `/jobs/{id}/start`, `/approve`, `GET /api/jobs[...]`.

## 9. Schedules (cron)

**Why:** Recurring agents — daily report, watch-and-summarize — while you sleep.
**How:** `schedules` table + a 60s scheduler loop that materializes only *due* jobs
(own 5-field cron matcher; overlap policy skip/allow). Failed step fails the job.
**Endpoints:** `POST/GET/PATCH/DELETE /api/schedules[...]`.

## 10. Tasks (kanban)

**Why:** Steerable per-task agent threads on a board (todo/doing/review/done).
**How:** `tasks` table; auto status doing→review, human marks Done. Each task has a
backing session/thread. (Unified under the jobs model.)
**Endpoints:** `GET/POST /api/projects/{slug}/tasks`, `PATCH/DELETE /api/tasks/{id}`.

## 11. Projects (workspaces)

**Why:** Scope agents to a folder — your real code, not a sandbox.
**How:** `projects` table. Create a scaffolded project OR **link an existing folder**
(`/api/projects/link`, jailed to configured link roots). Chat/terminal/files all
operate on the project path.
**Endpoints:** `GET/POST /api/projects`, `/projects/link`, `GET /api/fs/dirs`,
`PATCH/DELETE /api/projects/{slug}`.

## 12. Files workspace

**Why:** Browse/edit the whole project tree with live preview.
**How:** Tree + file read/write (CodeMirror), HTML/MD live preview, mkdir/rename/delete,
file upload, raw + token-scoped preview route (for images/SSE-safe URLs).
**Endpoints:** `/api/projects/{slug}/tree`, `/file`, `/upload`, `/fs/*`, `/raw`,
`/api/preview/{token}/{slug}/{path}`.

## 13. Run & Preview app

**Why:** Launch a project's dev server and preview it in-app.
**How:** `AppManager` runs one dev process per project; an authed reverse proxy serves
it. Folder field picks what to run.
**Endpoints:** `/api/projects/{slug}/app/start|stop|status`, `/apps`.

## 14. Image generation and retained media studios

**Active:** image generation remains available through `/image` (alias `/gambar`).
It uses the image provider selected in Settings, saves output under
`artifacts/media/images/`, and returns the artifact in the originating chat. Existing
image and media files remain readable through the normal artifact/file surfaces.

**Temporarily disabled by default:** Video and Design Studio remain in source but are
server-gated with `PROXIMA_FEATURE_VIDEO=0` and
`PROXIMA_FEATURE_DESIGN_STUDIO=0`. `GET /api/config` publishes the effective flags.
When disabled, the frontend omits their navigation, deep links, commands, settings,
provider health checks, artifact bridge actions, and agent guidance. Backend guards
return HTTP 503 with the `feature_disabled` payload before message creation, database
writes, provider calls, file writes, subprocesses, or collaboration dispatch.

The retained Studio implementation includes layered Konva scenes and exports; the
retained Video implementation includes generation providers and project artifacts.
Neither is an advertised or reachable capability in the default Proxima release.
Image generation does not expose *Edit in Design Studio* or *Add to Video Studio*
bridge actions while those features are disabled.

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
for hermes/codex). Disabled Video/Studio providers are omitted. Home shows a compact
"Connections" card beside System readout: green/red dot per check, with the
actionable fix detail on failures and a jump to Settings. Saving active provider
settings calls `auth_health.invalidate()` so the card re-checks on the next 5s poll.

## 19. Audit log

**Why:** An activity trail of meaningful actions.
**Endpoints:** `GET /api/audit`. (Roles/users management removed in single-user.)

## 20. Reliability (cross-cutting)

Heartbeat/reaper for hung runs, per-session serialization, graceful shutdown, output
salvage, orphaned-run cleanup, run timeout (configurable `run_timeout_seconds`, default
600s) + cancel-on-timeout, daily DB backup
(`proxima-backup` timer with `VACUUM INTO`).

## 21. Updates (version check + self-update)

**Why:** Every install should be one click away from the latest release without
the owner babysitting `git pull`.
**How:** `UpdateManager` polls GitHub Releases every 6h (silent failure — an
offline host, private repo, or GitHub hiccup never surfaces to the user); the
owner can also trigger a manual check from a **Check for updates** button in
Settings → Updates. When a newer release exists, the sidebar shows an update
pill that opens a release-notes modal (rendered markdown) with a one-click
**Update now**, which runs `scripts/proxima update` (`git pull --ff-only` +
rebuild + restart, tracked via an `update-status.json` marker file) behind a
blocking overlay that polls `/api/health` until the new version answers, then
reloads. Failures leave the old version running. **Windows:** check/notify
works the same; one-click apply is unsupported and returns a manual `git pull`
command instead.
**Endpoints:** `GET /api/update/status`, `POST /api/update/check`,
`POST /api/update/apply`.

---

## Removed (was multi-user, now single-user)

In-app user accounts, roles (`environment_admin`/`member`), login/password wall,
first-run team bootstrap, invite links, project membership/sharing, project
visibility (private/shared), team name. Collaboration model is instead: **everyone
self-hosts their own instance + shares folders/repos.** The runtime model is one
owner; legacy invite/member tables have been dropped.
