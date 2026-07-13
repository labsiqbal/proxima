# Proxima — Roadmap

## DNA (the filter)

Proxima is a **self-hosted control plane for the AI coding agents you already
own** — not an IDE, not a closed agent product. Every feature is judged against
five pillars. If a feature doesn't strengthen one of these, it doesn't ship.

1. **Bring-your-own-agent** — drives *your* local runners (Claude Code, Codex,
   Gemini, Hermes, local models) over ACP. No baked-in model, no lock-in.
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

Single-user cockpit · full-power chat (live agent config) · **Home dashboard** ·
multi-agent **brainstorm / debate** + **validate** sidecar · **workflows + jobs /
activity with cron schedules** · in-browser terminal · interactive approval/choice
cards · projects + **link existing folders** · agent profiles as personas
(per-profile instructions) · files + live preview · wiki + graph · dark/light +
design tokens · daily encrypted backup.

---

## Now — make daily use sharp (low/med effort, high DNA fit)

- **MCP management UI** — add/enable/configure MCP servers per profile/project
  from the app, no terminal round-trip. *(BYO-agent)*
- **Skills / slash-command palette** — browse and invoke the skills your runner
  has, from chat. Surfaces capability you already own. *(BYO-agent)*
- **Runner status panel** — which runners are installed/authed/ready, with a
  re-auth nudge. Removes the #1 "why no output" confusion. *(BYO-agent)*
- **Notifications that reach you** — PWA push (not just desktop) when an agent
  finishes or needs approval, so you can leave and get pinged. *(Cockpit)*
- **In-app Tailscale onboarding** — guided "serve + scan QR" for phone access.
  *(Self-hosted)*

## Next — the orchestration layer (the real differentiator)

- **Agent pipelines / handoff** — chain personas (research → build → review),
  output of one feeds the next; defined visually. *(Orchestration)*
- **Approval queue** — one place for every pending permission/question across all
  running agents. *(Orchestration + Cockpit)*
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
