# Design

Visual system for Proxima. Documents the **existing** implementation in `apps/web/src/styles.css` (single global stylesheet, CSS custom properties). The register is calm, compact, and task-first: the shell keeps real work in front without decorative command-center theater. Edit the tokens in `styles.css`; this file is the map.

## Theme & color strategy

- **Strategy: Restrained.** Tinted neutral surfaces + **one accent**. Accent is for primary actions, current selection, and state indicators **only** — never decoration.
- **Default accent:** `#0053fd` (a confident true-blue — `--theme-primary` / `--ui-accent`). Deliberately **not** the AI-2026 violet; violet exists only as an opt-in theme.
- **Light + dark are first-class.** Dark accent lightens to `#4f8cff`. Both must pass contrast.
- **Opt-in accent themes** (swap `--theme-primary`/`--ui-accent`): ocean `#0e7490`, violet `#7c3aed`, sunset `#ea580c`, forest `#15803d`. Default ships blue.

### Color roles (light → dark via `[data-theme]`)

| Role | Token | Light | Dark |
| --- | --- | --- | --- |
| Body / chat surface | `--ui-chat-surface-background` | `#fbfcfe` | `#0d1117` |
| Surface (cards) | `--ui-surface` | `#ffffff` | `#161b22` |
| Surface subtle | `--ui-surface-subtle` | `#f8fafc` | `#1c232c` |
| Sidebar | `--ui-bg-sidebar` | `#f7f9fc` | dark variant |
| Text primary | `--ui-text-primary` | `#111827` | light |
| Text secondary | `--ui-text-secondary` | `#4b5563` | — |
| Text tertiary (muted) | `--ui-text-tertiary` | `#8a94a6` | — |
| Strokes | `--ui-stroke-primary/secondary/tertiary` | `#cbd5e1` / `#dde5ef` / `#edf1f6` | — |
| Row hover / active | `--ui-row-hover/active-background` | accent @ ~5–10% | accent @ 12–20% |
| Accent / on-accent | `--ui-accent` / `--ui-on-accent` | `#0053fd` / `#fff` | `#4f8cff` |
| Danger | `--ui-danger` | `#dc2626` | — |
| Success | `--ui-success` | `#22c55e` | — |
| Warning (status) | `--ui-warning` / `--ui-warning-bg` | amber tokens | dark variants |

**Contrast rule:** body text ≥ 4.5:1, large/secondary ≥ 3:1. Muted (`--ui-text-tertiary`) is for meta/labels, never body prose. Status colors carry meaning (green=done/enabled, amber=review, blue=running/active, red=failed), used consistently.

## Typography

- **One family.** `--font-sans: Inter, ui-sans-serif, system-ui, …`. Mono: `--font-mono: ui-monospace, …` for code/paths only. No display/body pairing (product register).
- **Fixed rem scale** (not fluid/clamp): `--text-2xs .70` · `--text-xs .76` · `--text-sm .82` · `--text-base .90` · `--text-md 1.00` · `--text-lg 1.15` · `--text-xl 1.30` · `--text-2xl 2.00` (rem). Tight ratio (~1.12–1.2); the dashboard runs dense, headings are restrained.
- Weights: 400 body, 500 nav/labels, 600 emphasis/headings, 700 numerals/badges. No display weights in UI labels.
- Uppercase tracked (`.eyebrow`, `.group-toggle`) is reserved for **section/group labels only** (e.g. sidebar groups), not decorative kickers on content.

## Spacing & radius

- **Space scale:** `--space-1 4` · `-2 8` · `-3 12` · `-4 16` · `-5 20` · `-6 24` · `-8 32` (px). Vary spacing for rhythm; lean roomy (calm-by-default).
- **Radius:** `--radius-sm 8` · `--radius-md 12` · `--radius-lg 16` · `--radius-full 999`. Cards = lg, controls/rows = md, chips/inputs = sm, pills/dots = full.

## Motion

- **Scale (use these, don't invent):** `--ease: cubic-bezier(.2,.8,.2,1)` (standard ease-out) · `--ease-spring: cubic-bezier(.34,1.56,.64,1)` (press/pop) · durations `--t-fast 140ms` · `--t 220ms` · `--t-slow 360ms`.
- **Register: dashboard** — motion conveys STATE, not decoration. 140–240 ms on feedback; no orchestrated page-load. transform/opacity only (low-end hardware).
- **State-resolution craft (design-library §5.6):** every state resolves — button spring-press, card hover lift+shadow, modal scale-from-origin, segmented active-underline, status-pill/dot transitions, staggered list reveal (`.stagger-item` + `--i`), markdown step output, flow-diagram spine.
- **`@media (prefers-reduced-motion: reduce)` is global** and mandatory — kills all transitions/animations. Never gate content visibility on a transition.

## Components (established vocabulary — reuse, don't reinvent)

- **Buttons:** `.primary-button` (accent fill), `.ghost-button` (bordered subtle), `.icon-button`, `.row-action`. All carry hover + active(spring) + focus. Same shape everywhere.
- **Cards:** `.home-card`, `.kanban-card`, `.wf-card`, `.rail-card` — `--ui-surface`, lg radius, `--ui-stroke-secondary` border, hover = lift + shadow (not border-only). No nested cards.
- **Segmented control:** `.seg` / `.seg.sm` — active option gets the animated underline. Used for view/filter toggles.
- **Pills / dots:** `.pill`, `.job-pill` (status), `.sched-dot` / `.live-dot` (state). Full radius, status-colored.
- **Modals:** `.modal-scrim` (blur-in) + `.modal-card` (scale-from-center). Use sparingly — exhaust inline/progressive first.
- **Inputs:** bordered, md radius, accent focus-ring (`box-shadow 0 0 0 3px accent@18%`). Consistent control vocabulary.
- **Lists & rows:** `.home-list`, `.job-row`, `.nav-item` — title + muted meta; hover = row tint.
- **Flow diagram:** `.job-flow` (status-colored node spine) + `.job-flow-detail` (markdown output panel) — the legible-agent-work pattern.
- **Empty states teach** (`.home-empty`): say what the thing is + how to start, never "nothing here."
- **Loading:** skeleton/shimmer over spinners-in-content.

## Layout

- **App shell:** CSS grid — left sidebar (`--left-w` ~294px), the main pane, and a slim right tool rail (`--toolrail-w`). `.main-pane` scrolls; content is centered and uses the available width. Tool panels overlay the main pane from the right instead of claiming a grid column.
- **Responsive (structural, not fluid type):** sidebar becomes a drawer + `.mobile-topbar` ≤767px while the tool rail pins to the right edge; kanban 4→2→1 cols; the job-flow + side panel stack ≤860px.
- **New task launcher:** a single integrated Task Composer leads. Project/folder, Agent, attachments, image/design intents, and execution policy belong inside its chrome; destination dashboard cards do not render beneath it. At most one compact review-attention strip follows.
- **Density on demand:** roomy by default; tables/logs/data go dense only where the task needs it.

## New task launcher

The launcher (behind the Tasks screen's `+ New task`) is task-first, not a decorative command center. Its primary action creates a real project-scoped durable task through a dedicated composer. Project, Scheduled, Deliverables, and task monitoring live in their dedicated destinations rather than duplicate launcher cards. The launcher may show one truthful compact review-attention strip. The surface follows the active theme and shared token/component vocabulary.

**Do:** prioritize the brief, project context, truthful state, clear focus, and compact supporting information. **Don't:** add synthetic telemetry, always-dark command-center chrome, identical marketing cards, gradient text, or effects that compete with the task.

## Single-workspace shell ("Deck")

Proxima uses one compact workspace: subdued flow-ordered navigation on the left (Chat, Tasks, Recipes, Projects, Artifacts, feature-gated Design), a centered working surface, and a right icon rail holding the technical tools (Terminal, Files, Preview) as on-demand overlay panels. There is no Ops/Code switch; Chat is the front door and the default landing view. A New chat control in the chat header is the explicit action that clears the current session.

Recipes owns the plan canvas (Editor) and Scheduled modes; the old Sequential recipe editor is retired — a linear recipe is a graph without branches, authored on the same canvas. Tasks is the permanent execution/review index and each task opens a dedicated workspace. Agents and Settings stay in the account/profile menu; Wiki is under Settings → Knowledge & Wiki. Artifacts are project-owned outputs; Design is a separate, gated canvas destination.

Primary surfaces stay jargon-free: "agent" and "tools", never "runner"/"MCP"/"profile", env-var names, or raw stack traces — that detail lives in Settings and the docs.

The left navigation can collapse or resize by pointer or keyboard; mobile uses a drawer, and the tool rail stays reachable on the right edge.
