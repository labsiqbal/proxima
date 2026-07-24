# Proxima — Roadmap

## DNA (the filter)

Proxima is a **self-hosted control plane for the AI agents you already own** —
not an IDE, not a closed agent product. Every feature is judged against
five pillars. If a feature doesn't strengthen one of these, it doesn't ship.

1. **Bring-your-own-agent** - drives *your* local runners (Claude Code, Codex,
   Grok, Hermes, Pi - local models later) over ACP. No baked-in model, no lock-in.
2. **Orchestration** — many agents/personas working in parallel, steerable,
   with humans in the loop.
3. **Self-hosted & local-first** — your machine, your data, your keys. Privacy by
   default; remote via your own Tailnet.
4. **Cockpit above the terminal** — visualize, organize, and direct work you'd
   otherwise do blind in a shell. Reach it from any device.
5. **Knowledge continuity** — wiki + memory that persist and compound across
   sessions and projects.

> Anti-goals (don't build): out-editing VS Code/Cursor (autocomplete, debugger),
> out-hosting Replit (cloud build/deploy infra), or any baked-in proprietary
> model. Those are losing fights against their own turf.

---

## Shipped (foundation)

Single-user cockpit · full-power Chat with approvals and turn restore · **Alpha
orchestration desk** with three worker slots, checkpoints, budgets, and optional
unattended starts · global **Attention** inbox · multi-agent brainstorm / debate +
validate sidecar · workflows, jobs, and cron schedules · reviewable workflow
graphs · in-browser Terminal, Files, and Preview · projects and linked folders ·
agent profiles with ready/not-ready runner status · Claude Code, Codex, Grok,
Hermes, and Pi runners · wiki + graph · Design Studio · themes and PWA · daily
integrity-checked DB backup.

**Phase 1 complete (slices 1-12):** single workspace around the flow (Chat or
Alpha → Tasks → Recipes, tools on a right rail) · project work containers (code
areas + ops area) · run-first plans with per-job targets · repo jobs in isolated
worktrees with in-app diff review + local merge · turn-timeout auto-continuation ·
deterministic script nodes with hash-bound trust · durable Archive registry
(lineage, synced approval, permalinks) · bundled capability pack (masterplan skill
+ work-discipline preamble) · flow-centric positioning with the honest
two-sentence security note · BYO repo-remote connector (per-area push-after-merge,
default off) · satpam supervision loop (stalled/looping/confused detection, steer
→ gated restart-clean → escalate, decision-hold with independent branches
continuing).

**Current product layer:** first-class `/masterplan` intake · official Grok Build
CLI over native ACP · ArtifactViewer v2 with point annotations, rendered Mermaid,
editable Excalidraw conversion, and feedback handoff to the producing Chat ·
replayable core tour and feature-aware Help chapters.

---

## Now — make daily use sharp (low/med effort, high DNA fit)

- **MCP config editing UI** — detection plus per-profile enable/disable is shipped for
  Claude, Codex, Grok, and Hermes; add/edit/test server definitions still uses each runner's
  native config and remains a future convenience. *(BYO-agent)*
- **Broader skills palette** - `/masterplan` is shipped as a first-class command;
  add browse-and-invoke entries for the other skills each runner already owns.
  *(BYO-agent)*
- **Notifications that reach you** — PWA push (not just desktop) when an agent
  finishes or needs approval, so you can leave and get pinged. *(Cockpit)*
- **In-app Tailscale onboarding** — guided "serve + scan QR" for phone access.
  *(Self-hosted)*
- **Relocate a project folder** — change a project's linked path from settings
  (reuse the onboarding folder-picker) when you move/rename its folder on disk;
  history stays since it's keyed by project id and Proxima reads the folder live —
  only the stored path is stale. Bonus: detect a project whose folder has gone
  missing and offer to re-point it instead of breaking silently. *(Cockpit, Self-hosted)*

## Next — the orchestration layer (the real differentiator)

- **Agent pipelines / handoff** — shipped in Phase 1 as plans: a DAG of jobs
  with per-node agents, outputs feeding dependents, drawn on the canvas. What
  remains here: richer persona-to-persona handoff conventions. *(Orchestration)*
- **Local model runner** — Ollama / vLLM via 9router as a first-class runner, for
  truly no-cloud operation. *(Self-hosted, BYO-agent)*

## Later — compounding + scale

- **Knowledge: cross-project wiki + global search**, auto-distill sessions into
  notes, surface & edit agent memory. *(Knowledge)*
- **Run history / trajectory replay** — inspect or rerun what an agent did.
  *(Cockpit)*
- **Cost & usage tracking** per profile/project/session. *(Cockpit)*
- **Deploy/restore UX** — one-command mini-PC setup, backup browser & restore.
  *(Self-hosted)*
- **Connectors vault** — manage credentials/keys (incl. rotation) from the app,
  wired to runners + MCP. *(Self-hosted)*
- **OSS hardening** (only if going multi-user/public): split the monolith API,
  frontend tests, real auth, per-OS-user isolation. *(Foundation)*

---

## Sequencing logic

Build **Now** first (it makes the thing you use every day better and is cheap).
Then invest in **Next** — orchestration is the moat; it's what makes Proxima a
*control plane* and not just a nicer terminal. **Later** is for when it's a daily
driver worth scaling. Re-run the DNA filter on every new idea before it enters a
phase.
