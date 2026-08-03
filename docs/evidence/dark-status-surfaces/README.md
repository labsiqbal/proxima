# Dark-theme danger / warning / success surfaces (#155)

Captured from a **disposable** `/tmp` render: `harness.html` (kept here) links the
real `apps/web/src/styles.css` — the committed one for *after*, `git show HEAD:` for
*before* — and marks up every rule in the stylesheet that consumes a
`--ui-danger*`, `--ui-warning*` or `--ui-success*` token. Headless Chrome at
1680×2600, `data-theme` set per capture. No app instance, no database, no
`HOME`, and **zero writes into any linked real folder**; the live service was
not touched.

A static harness rather than a live instance because this is a tokens-only
change: it puts all ~50 status surfaces on one page at once, which no single
app screen can do, and it renders the exact stylesheet that ships.

| | Light | Dark |
|---|---|---|
| Every status surface, before | `before-light.png` | `before-dark.png` |
| Every status surface, after | `after-light.png` | `after-dark.png` |
| Destructive button detail | `btn-after-light.png` | `btn-after-dark.png` |
| Sunset (the default preset), after | `after-sunset.png` | — |

## What the captures show

`before-dark.png` is the bug: the dark preset never restated the semantic tints,
so `--ui-danger-bg: #fef2f2`, `--ui-success-bg: #f0fdf4` and
`--ui-warning-bg: #fffbeb` were still in force on a warm-charcoal app. Every
error, warning and success panel is a near-white card — Master error banners,
the toast repeat count, app-runner refusal cards, Ops migration messages, Satpam
approvals, review/job/graph badges, diff lines, collab failure cards, the debug
panel, the project banners. Several carry light-theme *text* colours on top and
are unreadable outright ("App is stopped", "Migration applied", the Satpam
reason, the Master decision response, the run-result card, the project context
row).

`after-dark.png` is the same page with the preset completed. Ratios measured on
the resulting tint clear WCAG AA and beat the light theme's own for the same
pair: danger 4.59 / -strong 6.00 / -text 8.11, warning 5.55 / -text 6.70,
success 6.34 / -strong 7.89.

## What the captures assert, not just show

- **Light did not move.** `before-light.png` vs `after-light.png` differ by 469
  pixels out of 4.4M — hue shifts from the literal→token swaps
  (`#2da44e`→`--ui-success-strong`, `#b45309`→`--ui-warning-text`, and the
  `rgba()` borders), all sub-perceptual at 1:1.
- **The other presets had no gap.** Ocean, Violet, Sunset and Forest are
  light-surfaced, so they inherit the `:root` palette. Rendered and diffed
  against Light, the only differing pixels are accent-coloured chrome
  (905–1094 px, all inside the one cell holding the accent-tinted `running`
  pill and toast rails). `after-sunset.png` is kept because Sunset is
  `DEFAULT_THEME`.
- **The destructive button keeps a readable label in both themes.** Dark uses
  `--ui-danger-fill` (white label 4.63:1, fill vs surface 3.06:1) rather than
  the bright text-tuned hue, which would have put white at 2.2:1.

`apps/web/src/theme.tokens.test.ts` is the durable guard; these frames are the
one-time proof.
