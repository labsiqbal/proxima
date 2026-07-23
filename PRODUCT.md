# Product

## Register

product

## Users

A **solo operator** — a founder, indie builder, or one person on a small team — who self-hosts to run a mix of **human + AI-agent** work. **Single-user: one owner per install**, no seats/accounts/invites — the network layer is the access boundary. They install Proxima on their own machine/server and reach it privately (LAN, Tailscale, or a Cloudflare-tunneled subdomain). They are mid-technical at most — comfortable installing a tool, but they want the cockpit itself to be effortless. They are *in a task*: starting a chat, running a workflow, reviewing an agent's output, checking what's scheduled. The owner is often non-technical and delegates the actual building to agents — so the UI must make agent work legible and controllable without jargon.

## Product Purpose

Proxima is a **self-hosted, single-user control plane / cockpit** for delegating real work to the AI agents the owner already owns: think it through in **Chat**, let AI slice it into reviewable jobs, watch the plan run in **Tasks** (with review gates, and local diff review + merge for jobs that touch a repo), and keep the good runs as repeatable **Recipes** - runnable on demand or on a cron schedule - with every deliverable landing in the **Archive** as a durable record. Code, content, and ops flow through the same loop. It is runner-agnostic (drives Claude Code / Codex / Grok / Hermes / Pi over ACP) and BYO-AI (no per-seat SaaS, no baked-in model or credentials). Success = the owner trusts it to run real work end-to-end, sees exactly what the agents did, and never feels they've left "their own machine." Open source (AGPL); each user self-hosts their own single-owner cockpit.

Image generation and Design Studio are part of the active product (Design Studio
sits behind a server-owned feature flag — on by default in dev, opt-in on
installed instances). Video Studio was removed.

## Brand Personality

**Powerful through real work, not theater.** Proxima uses one calm, compact product register in a single workspace: flow-ordered navigation (Chat, Tasks, Recipes, Projects, Archive) with the technical tools (Terminal, Files, Preview) on a right rail. Chat is the front door; Tasks, Recipes, the Archive, and project knowledge stay focused and uncluttered. Character comes from precise hierarchy, truthful live state, and responsive interaction - not synthetic mission-control visuals.

Voice: plain and direct, with operator energy (uppercase mono labels, status lines), never hypey marketing.

3 words: **powerful · alive · owned.**

## Anti-references

Bold is welcome; *cheap* and *generic* are not. Do NOT look like:

- **Generic "AI 2026"** — cream/sand/beige body backgrounds with a purple/violet accent; the warm-neutral monoculture. Default accent stays blue.
- **Stiff corporate/enterprise** — navy-and-gold, cold formal admin panels, "legacy IBM dashboard" chrome.
- **Cheap-SaaS clichés** — the hero-metric template, identical icon-heading-text card grids, eyebrow kickers on every section, gradient text. Expressive ≠ these tired patterns.
- **Thoughtless clutter** — density only where the task needs it; supporting information must not crowd out the task.
- **Too plain / sterile** — hierarchy and interaction should still make the workspace feel deliberate, while the task remains the focus.

## Design Principles

1. **The tool disappears into the task.** Earned familiarity over novelty (Linear/Notion/Stripe-grade trust). Reuse the established component vocabulary screen-to-screen; invent affordances only when the UX genuinely wins.
2. **Resolve every state.** Premium = the in-between. Hover/active/open/close/status-change all ease (140–240 ms, transform/opacity, dashboard register). Nothing snaps; nothing is decorative-only.
3. **Make the agents' work legible.** Show real work, live (flow diagrams, step output, run history) so a non-technical owner can see and control what's happening — never a black box.
4. **Calm by default, density on demand.** Generous breathing room and a quiet surface first; reveal dense data (tables, logs) only where the task needs it.
5. **Private and owned.** No dark patterns, no rented-SaaS tells. Respect the user's machine: perf-conscious (the target hardware is low-end), offline-friendly, their data in their files.

## Accessibility & Inclusion

- Body text ≥ 4.5:1 contrast; large/secondary text ≥ 3:1 — no light-gray-on-tinted-white.
- **`prefers-reduced-motion` honored on every animation** (already wired globally) — motion is enhancement, never required to read content.
- Keyboard-operable controls; visible focus rings.
- **Low-end hardware is a first-class constraint** (typical host: i5-7200U class). Transform/opacity-only motion, no heavy effects by default.
- Light + dark themes (plus accent themes) must both pass contrast.

### A calmer task-first workspace

Chat is the default landing view and the front door for new work. The New task launcher (behind Tasks) is a focused brief and project picker backed by a real ad-hoc job - no destination dashboard grid, no decorative command center, no synthetic activity. Attention-needing work surfaces on the Tasks index where it can be acted on. This keeps Proxima powerful, alive, owned, and runner-agnostic, and Chat never loses its current session merely because the owner visits another screen.
