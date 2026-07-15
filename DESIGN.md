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
| Chrome / sidebar | `--ui-bg-chrome` / `--ui-bg-sidebar` | `#f4f7fb` / `#f7f9fc` | dark variants |
| Text primary | `--ui-text-primary` | `#111827` | light |
| Text secondary | `--ui-text-secondary` | `#4b5563` | — |
| Text tertiary (muted) | `--ui-text-tertiary` | `#8a94a6` | — |
| Strokes | `--ui-stroke-primary/secondary/tertiary` | `#cbd5e1` / `#dde5ef` / `#edf1f6` | — |
| Row hover / active | `--ui-row-hover/active-background` | accent @ ~5–10% | accent @ 12–20% |
| Accent / on-accent | `--ui-accent` / `--ui-on-accent` | `#0053fd` / `#fff` | `#4f8cff` |
| Danger | `--ui-danger` | `#dc2626` | — |
| Success | `--ui-success` | `#22c55e` | — |
| Warning (status) | (literal) | `#d29922` | — |

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

- **App shell:** CSS grid — left sidebar (`--left-w` ~294px) and the main pane. `.main-pane` scrolls; content is centered and uses the available width.
- **Responsive (structural, not fluid type):** sidebar becomes a drawer + `.mobile-topbar` ≤767px; kanban 4→2→1 cols; home grid 2→1 col; the job-flow + side panel stack ≤860px.
- **Ops Home:** a single integrated Task Composer leads. Project/folder, Agent, attachments, image/design intents, and execution policy belong inside its chrome; destination dashboard cards do not render beneath it. At most one compact review-attention strip follows.
- **Density on demand:** roomy by default; tables/logs/data go dense only where the task needs it.

## Ops Home

Home is the task-first Ops surface, not a decorative command center. Its primary action creates a real project-scoped durable task through a dedicated composer without Code collaboration pills. Project, Scheduled, Deliverables, and task monitoring live in their dedicated destinations rather than duplicate Home cards. Home may show one truthful compact review-attention strip and never mixes ordinary Code sessions into Ops. The surface follows the active theme and shared token/component vocabulary.

**Do:** prioritize the brief, project context, truthful state, clear focus, and compact supporting information. **Don't:** add synthetic telemetry, always-dark command-center chrome, identical marketing cards, gradient text, or effects that compete with the task.

## Compact Ops / Code shell

Proxima uses a compact two-region desktop shell: subdued navigation on the left and a centered working surface. The Ops/Code switch changes working register without changing the underlying job or chat lifecycle. Ops is task-first and calm; Code preserves the current chat session. A New session control in the Code header is the explicit action that clears the current session.

The sidebar adapts by workspace. Ops contains New task, Tasks, Projects, one Workflows destination, Artifacts, feature-gated Design, and an Advanced group for Video. Workflows itself contains Sequential, feature-gated Advanced, and Scheduled modes. Code contains New session, Projects, Terminal, and recent sessions. Tasks is the permanent Ops execution/review index and each task opens a dedicated workspace. Agents and Settings stay in the account/profile menu; Wiki is under Settings → Knowledge & Wiki. Artifacts are project-owned outputs; Design is a separate, gated canvas destination.

The left navigation can collapse or resize by pointer or keyboard; mobile uses a drawer and a narrow single-column Ops dashboard.
