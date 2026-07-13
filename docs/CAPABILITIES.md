# Proxima — Capability Map

What's built, why it exists, and how it works. A reference for understanding what
this cockpit is actually capable of. (Derived from the code, not aspirational.)

> **Where do I edit when I add/change a feature?** See the companion
> [reference/feature-map.md](reference/feature-map.md) — a per-feature grid of
> code locations (backend + frontend), tables/events touched, relations, and
> status/flag. This doc explains *what & why*; that one maps *where*.

> **Model:** single-user cockpit. One owner, no in-app accounts. The primary access
> gate is the network (loopback / Tailscale / Cloudflare Access); on top of that the
> owner sets a **password** on first run and every request then needs a valid session
> (bearer token or the HttpOnly `proxima_session` cookie) — defense-in-depth, not
> multi-tenancy (see §21). Until a password is set, `POST /auth/auto` grants a
> passwordless session (network-only mode); once set, clients use `POST /auth/login`.
> Per-user data lives outside the repo (`~/.local/share/proxima/`).

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

> **Status:** the `Brainstorm`/`Debate` chips are shown in the **main chat** only.
> The Design/Video Studio composers disable them (studio chats are single-agent).

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

## 10. Projects (workspaces)

**Why:** Scope agents to a folder — your real code, not a sandbox.
**How:** `projects` table. Create a scaffolded project OR **link an existing folder**
(`/api/projects/link`, jailed to configured link roots). Chat/terminal/files all
operate on the project path. On **first run**, right after setting a password, an
onboarding step (`screens/WorkspaceOnboarding.tsx`, reusing the `FolderLinker`
browser) offers to link a real code folder before landing in the app; skipping
uses the starter project auto-provisioned under the data dir.
**Endpoints:** `GET/POST /api/projects`, `/projects/link`, `GET /api/fs/dirs`,
`PATCH/DELETE /api/projects/{slug}`.

## 11. Files workspace

**Why:** Browse/edit the whole project tree with live preview.
**How:** Tree + file read/write (CodeMirror), HTML/MD live preview, mkdir/rename/delete,
file upload, raw + token-scoped preview route (for images/SSE-safe URLs).
**Endpoints:** `/api/projects/{slug}/tree`, `/file`, `/upload`, `/fs/*`, `/raw`,
`/api/preview/{token}/{slug}/{path}`.

## 12. Run & Preview app

**Why:** Launch a project's dev server and preview it in-app.
**How:** `AppManager` runs one dev process per project; an authed reverse proxy serves
it. Folder field picks what to run.
**Endpoints:** `/api/projects/{slug}/app/start|stop|status`, `/apps`.

## 13. Design Studio & image generation

**Why:** AI drafts **editable, layered** designs (text stays real text; images/shapes
are separate layers) that a human refines on a Konva canvas and exports — not flat,
baked-in-pixel images. Full blueprint: [DESIGN-STUDIO.md](DESIGN-STUDIO.md).

**Status:** Design Studio is a **first-class, enabled feature** — on by default in dev
(`scripts/dev` sets `PROXIMA_FEATURE_DESIGN_STUDIO=1`) and off by default in the
packaged install (set the flag to enable). **Video** is retained-but-off everywhere
(`PROXIMA_FEATURE_VIDEO=0`). `GET /api/config` publishes the effective flags; a disabled
feature is omitted from nav/deep-links/commands/settings/health-checks/agent-guidance,
and its backend guards return HTTP 503 `feature_disabled` before any side effect
(message creation, DB/file writes, provider calls, subprocesses, collaboration).

**How:**
- **Seed from chat:** `/design <brief>` (back-compat aliases `/image-studio`,
  `/design-studio`) or the composer's ✨ Generate → **Design draft** opens a linked
  design session that arrives already designed, not blank. (`routes/chat.py`
  `_chat_media_kind`, `commands.py`, `features.py`, `Composer.tsx`.)
- **Asset- & feature-aware agent, with vision:** `buildDesignPrompt`
  (`components/design/scene.ts`) injects the project's asset library (agent can place
  existing assets by exact path) and the full feature set, and attaches relevant images
  so a vision-capable model can **see** them. Vision rides ACP image content blocks,
  capability-gated: `acp.py` captures `promptCapabilities.image`; the run appends a
  `⟦VISION:…⟧` marker that `run_prompting.extract_vision_images` reads; `worker.py`
  passes the bytes to `proc.prompt(images=…)` only when the runner advertises image
  support (else text-only). The agent replies with `<design-scene>` blocks the canvas
  applies live.
- **Multi-image edit / compose (Assets tab):** an input tray attaches several images
  (`@image1`, `@image2`, … addressable by name in the prompt) to edit or compose into
  one, gated on the image provider's `referenceImages` capability. AI image edit now
  lives in the Assets tab (moved out of the right inspector). (`ImageGenRequest.images`,
  `image_providers.generate` `extra_images`, `routes/design.py`.)
- **Editing UX:** custom color picker (`ColorInput`) everywhere with an **eyedropper**
  (native `EyeDropper` API + a canvas-sampling fallback for non-secure hosts), an
  **on-canvas gradient direction guide** (draggable line), collapsible left/right
  panels, restore-last-design on return, and crop preview.

**Image generation** (available independent of the flag): `/image` (alias `/gambar`)
uses the Settings image provider, saves under `artifacts/media/images/`, and returns the
artifact in the originating chat. The `codex` provider now supports image **edit +
reference images** (was text-to-image only). Existing media stays readable as ordinary
artifacts.
**Endpoints:** `POST /api/projects/{slug}/design/image`,
`POST /api/projects/{slug}/designs/from-image`,
`GET /api/projects/{slug}/design/image-models`.

## 14. Wiki + memory (knowledge)

**Why:** Per-project + global knowledge that compounds across sessions.
**How:** Markdown files under each project's `wiki/`; a built index + tree; global
aggregation. Fed by Chat→Wiki (§5).
**Endpoints:** `/api/projects/{slug}/wiki/all`, `/api/wiki/all`, `/tree`, `/file`, `/fs/*`.

## 15. Terminal

**Why:** A real shell in the cockpit, scoped to the project.
**How:** `terminal.py` over `WS /api/ws/terminal`.

## 16. Command palette (quick commands)

**Why:** A catalog of quick slash-style commands runnable from chat.
**How:** `commands.py` — `command_catalog()` lists them; `execute_command()` runs one.
**Endpoints:** `GET /api/commands/catalog`, `POST /api/commands/execute`.

> Note: an earlier *advisory command-policy classifier* (`POST /api/policy/command/check`)
> was **removed** — it never gated real agent/tool execution (the agent runs its own
> shell inside the runner CLI, not through this API), so it gave a false impression of a
> guard. The real access boundary is network reachability (single-user). See
> [security-boundaries.md](security-boundaries.md).

## 17. Home dashboard + search

**Why:** The cockpit's cockpit — pulse of runs/jobs/projects; jump anywhere.
**How:** `/api/dashboard` returns counts plus a `jobsByStatus` breakdown
(queued/running/review/done) that Home renders as a jobs-by-status bar (the old
per-task board is gone — Jobs/Activity replaced it), recent artifacts, pending
approvals, and system/auth health.
**Endpoints:** `GET /api/dashboard`, `/api/runs/active`, `GET /api/search`.
**Connections card (auth health):** `/api/dashboard` includes `authHealth` — cached
background checks (`auth_health.py`, 60s TTL, never on the request path) of the
selected image provider plus every runner referenced by a profile (deep auth check
for hermes/codex). Disabled Video/Studio providers are omitted. Home shows a compact
"Connections" card beside System readout: green/red dot per check, with the
actionable fix detail on failures and a jump to Settings. Saving active provider
settings calls `auth_health.invalidate()` so the card re-checks on the next 5s poll.

## 18. Audit log

**Why:** An activity trail of meaningful actions.
**Endpoints:** `GET /api/audit`. (Roles/users management removed in single-user.)

## 19. Reliability (cross-cutting)

Heartbeat/reaper for hung runs, per-session serialization, graceful shutdown, output
salvage, orphaned-run cleanup, run timeout (configurable `run_timeout_seconds`, default
600s) + cancel-on-timeout, daily DB backup
(`proxima-backup` timer with `VACUUM INTO`).

## 20. Updates (version check + self-update)

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

## 21. Access & auth (single-user password gate)

**Why:** Defense-in-depth on top of the network boundary — a reachable API should still
ask for a password. Not multi-tenancy; there is still exactly one owner.
**How:** On first run the owner sets a password (`/auth/set-password`); after that every
request needs a valid session, carried as a bearer token or the **HttpOnly
`proxima_session` cookie** (`routes/auth.py`). The cookie is the persistent auth for the
SPA, SSE stream, terminal/preview/appview WebSockets — a terminal WS now requires a
valid session — so auth no longer rides `?token=`/`localStorage`. `/auth/auto` grants a
passwordless session only until a password exists (network-only mode); once set, clients
use `/auth/login`. `/auth/logout` clears the cookie; `/auth/change-password` rotates it.
Local recovery is `scripts/reset-password`.
**Endpoints:** `POST /auth/set-password`, `/auth/login`, `/auth/logout`,
`/auth/change-password`, `/auth/auto`, `/auth/resume`.

---

## Removed (was multi-user, now single-user)

In-app user accounts, roles (`environment_admin`/`member`), the multi-user login wall,
first-run team bootstrap, invite links, project membership/sharing, project
visibility (private/shared), team name. (A single-user password gate remains as
defense-in-depth — see §21 — but there are no accounts or roles behind it.)
Collaboration model is instead: **everyone
self-hosts their own instance + shares folders/repos.** The runtime model is one
owner; legacy invite/member tables have been dropped.
