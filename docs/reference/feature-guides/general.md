# GENERAL_GUIDE — the "how to operate" layer

> **Status: WIRED (2026-07-05)** as `GENERAL_GUIDE` in `wiki_memory.py`, injected at the
> top of `build_run_preamble()` (both project and no-project branches), before
> feature-specific guides. Disabled feature guides are not composed. ≈610 tokens,
> once per session. Register-neutral: a profile's own
> instructions override it. Keep this doc and the constant in sync.
>
> **Cross-runner verified (2026-07-05):** fed the REAL `build_run_preamble` output +
> one identical task ("onboarding brief for this project") to both the Codex and Claude
> CLIs (the same binaries SB drives) in the same test project. Both, from the identical
> guide: (a) oriented — read README/src/wiki, brief was project-specific; (b) routed the
> deliverable to `artifacts/` (Claude → `artifacts/onboarding.md`, Codex →
> `artifacts/reports/onboarding-brief.md`); (c) reported evidence-first (Codex printed a
> `Verification:` block of the commands it ran and honestly flagged that git failed on the
> empty `.git`; Claude flagged the repo is a bare skeleton and offered next steps). Codex
> also grew the wiki per the memory norm.

## Why

Today the general layer of the run preamble is literally two lines ("You are running
inside Proxima… working directory is this project"). Every work norm is unstated:
no output routing (agents can scatter files in the project root), no evidence-first
rule, no ask-vs-act boundary, no tool/skill awareness, no reporting style. Feature
guides (design/video) cover their own lanes; nothing covers *being a good agent in
this workspace*.

Deliberately NOT here: persona/character — that's the Profile instructions layer
(injected above this, user-authored). This layer stays register-neutral so it never
fights a profile.

## The prompt text (draft)

<!-- BEGIN PROMPT -->
```text
## Working in Proxima (how to operate — all sessions)
Your replies render as markdown in a chat UI. Files you write into the project can
surface to the user as clickable result cards (see "Where output goes").

### Know the project first
- Your working directory IS the project; its files are the ground truth. Before any
  substantive work, orient yourself: README/docs, the folder structure, and the project
  wiki notes included in this context — then act consistent with what exists.
- Adopt the project's conventions (naming, layout, stack). Don't impose new structure
  on a project that already has one.

### Use your full toolkit
- You run inside your own agent runtime with its native abilities: shell, file tools,
  and whatever skills, MCP tools, and commands this profile has enabled. Discover what
  you actually have before working, and prefer an existing skill or tool over rebuilding
  what it already does.
- If the task needs a capability you don't have, say so plainly and propose the closest
  alternative — never fake a result.

### Where output goes
- User-facing deliverables belong under artifacts/ (or reports/ / exports/) so
  Proxima surfaces them as result cards: generated images →
  artifacts/media/images/, documents/reports → a .md/.pdf/.html file under
  artifacts/, runnable apps → their own folder with a package.json (auto-detected).
- Code changes follow the project's existing structure. Never scatter ad-hoc files in
  the project root.

### Evidence-first completion
Never claim done without evidence: name the files changed/created, the commands run and
their actual results, and what you verified. If a step failed or was skipped, say so —
a truthful partial result beats confident fiction.

### Ask vs act
- Reversible, in-scope work: just do it. For choices that shape the outcome — scope,
  creative direction, anything destructive or broad, anything outside this project —
  ask first with a compact <question-form> and offer a sensible default.
- Never print secrets/tokens/credentials into chat or logs, and don't open credential
  or config files unless the task genuinely requires them.

### Reporting
Lead with the outcome in plain language (what exists now that didn't before), then the
evidence, then next options. Keep it tight; give artifact paths so cards resolve.

Your profile instructions and this project's own conventions override these defaults
wherever they conflict.
```
<!-- END PROMPT -->

≈ 430 tokens, once per session. Placement: top of `build_run_preamble` output (both the
with-project and no-project branches), directly under the `[Proxima context]` marker
+ project line.

## The skills gap — RESOLVED (auto-detect + activation, shipped 2026-07-05)

Requirement: agents should be aware of and use the skills + MCP installed in their
runtime. Originally the seeded runner homes carried credentials only, so "when present"
was aspirational. **Built 2026-07-05** as a portable, per-runtime feature (option 2, the
per-profile-selection path — chosen over a hardcoded symlink because a fixed path isn't
portable across machines):

- **Detection** (`capabilities.py::detect_for_runner`) reads each runner's OWN config
  dir (driven off `RunnerSpec.source_dir`, `~`-relative → portable): claude skills =
  `~/.claude/skills/*` (flat + grouped `category/skill`) + MCP from `~/.claude.json`;
  codex MCP from `~/.codex/config.toml`; hermes skills dir + `config.yaml`. No absolute
  path hardcoded anywhere but the per-runner rules.
- **Selection** — per-profile, stored in `profiles.capabilities` (JSON). NULL = inherit
  ALL detected (host skills just work); explicit lists = subset/opt-out.
- **Activation** (`apply_capabilities`) symlinks the selected skills into the profile
  home and filters its MCP config to the selection. Runs on create, on capability
  change, and idempotently before every run (`RunPrompting.reapply_capabilities`) so
  newly installed host skills appear automatically and pre-feature profiles self-heal.
- **UI** — a "Skills & MCP" section per profile card (ProfilesScreen) lists what the
  runtime has, with checkboxes.
- **API** — `GET /api/runners/{id}/capabilities` (detected), `PATCH /api/profiles/{id}`
  `capabilities` (selection).

So the guide's "when present" is now real. Rejected: env-passthrough to the host config
dir (would expose all host creds/sessions to a prompt-injectable runner — against
`docs/prompt-injection-hardening.md`).

## Open iteration points

1. **Overlap with profile instructions** — general stays neutral; if a user's profile
   contradicts a norm here (e.g. wants raw logs), the profile should win. Add one line
   "your profile instructions override these defaults where they conflict"? Lean: yes.
2. **Secrets line vs prompt-injection-hardening** — the hard enforcement is server-side
   policy; this line is belt-and-suspenders in the prompt. Keep.
3. **Token cost** — ~430 tokens on every session incl. tiny Q&A chats. Acceptable;
   revisit only if users complain about latency on first turn.
4. **Live test** — same harness as design: lazy prompt on staging, check the agent (a)
   orients before acting, (b) routes output to artifacts/, (c) reports evidence-first.

## Queue

- `video.md` — next after this wires
- skills-seeding wiring task (option 1) — small, separate commit
