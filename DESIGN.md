# Design

Visual system for Proxima. Documents the **existing** implementation in `apps/web/src/styles.css` (single global stylesheet, CSS custom properties). Register: dual — **characterful command-center** for signature surfaces (Home / Command Center), **calm and focused** for working tools (the tool disappears into the task). (See PRODUCT.md; the earlier "premium-minimal" framing was dropped as too plain.) Edit the tokens in `styles.css`; this file is the map.

## Theme & color strategy

- **Strategy: Restrained.** Tinted neutral surfaces + **one accent**. Accent is for primary actions, current selection, and state indicators **only** — never decoration.
- **Default accent:** `#0053fd` (a confident true-blue — `--theme-primary` / `--ui-accent`). Deliberately **not** the AI-2026 violet; violet exists only as an opt-in theme.
- **Light + dark are first-class.** Dark accent lightens to `#4f8cff`. Both must pass contrast.
- **Opt-in accent themes** (swap `--theme-primary`/`--ui-accent`): ocean `#0e7490`, violet `#7c3aed`, sunset `#ea580c`, forest `#15803d`. Default ships blue.

### Color roles (light → dark via `[data-theme]`)

| Role | Token | Light | Dark |
|---|---|---|---|
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

- **App shell:** CSS grid — left sidebar (`--left-w` ~294px) · main pane · right rail (`--right-w` ~292px). `.main-pane` scrolls; content capped ~1060px and centered on wide screens.
- **Responsive (structural, not fluid type):** right rail hidden ≤1180px; sidebar becomes a drawer + `.mobile-topbar` ≤767px; kanban 4→2→1 cols; home grid 2→1 col; the job-flow + side panel stack ≤860px.
- **Home dashboard:** deterministic 2-col grid — Activity (full) → Workflows | Scheduled → Tasks | Recent(tall) → Projects.
- **Density on demand:** roomy by default; tables/logs/data go dense only where the task needs it.

## Signature surfaces — Command Center (the Home)

The Home is the **brand moment**: a deliberate **dark "command-center"** hero, independent of the app theme, that contrasts with the lighter task tools around it. Pattern (see `.home-command` / `.cmd-*` in `styles.css`):

- **Always-dark surface:** near-black `#070a0f`, ink `#e9eff7`, dim `#707c8c`, hairline `rgba(255,255,255,.075)` (scoped `--cmd-*` vars). Not theme-flipped.
- **Accent = neon.** The active theme's `--ui-accent` is the glow colour (so it still personalizes). Glow via `text-shadow` / SVG `feGaussianBlur` / `box-shadow` with `color-mix(... var(--ui-accent) …%, transparent)`. Green `--ui-success` = live/online glow.
- **Depth, not flat:** radial accent glows + a faint masked grid texture (`.cmd-bg`) behind translucent panels (`linear-gradient(rgba white .03→.01)` + hairline border).
- **Operator typography:** `--font-mono` for labels, readouts, timestamps, status lines — uppercase, wide tracking. Inter for the big focal headline.
- **Glowing data viz:** SVG area+line with a glow filter; the line **draws in** (`pathLength=1` + `stroke-dashoffset` 1→0). Mono readouts count up. Live dots blink/pulse.
- **Restraint still applies:** it breathes (glow + space), it is not cluttered; reduced-motion kills the draws/pulses.

This dual register is intentional: **signature surfaces are bold; working tools stay focused** (the established light/dark product system above).

## Do / Don't (Proxima specific)

**Do:** reuse the `--ui-*` tokens + component classes for **working tools**; give **signature surfaces** real presence (the command-center pattern); use the accent as the one bold/glow colour; resolve every state with the motion scale; respect reduced-motion + low-end hardware (transform/opacity, glow stays cheap).

**Don't:** the AI-2026 cream-bg + violet-accent monoculture (default accent stays blue; signatures go dark); stiff corporate navy-gold; **cheap-SaaS clichés** (hero-metric template, identical icon-heading-text card grids, eyebrows-everywhere, gradient *text*); thoughtless clutter; or the opposite failure — a **flat, characterless, all-white** dashboard with no presence. Side-stripe borders and default decorative glassmorphism remain banned.
