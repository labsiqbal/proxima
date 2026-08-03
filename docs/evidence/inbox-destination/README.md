# Inbox destination and ephemeral header notifications (#157 / #158)

Captured from a **disposable** instance - its own database, `HOME`, workspace and
link root under a temp directory that is deleted at the end of the run. No writes
into any linked real folder, and the live service was not touched.

The fixture seeds one of each kind the ledger has to carry: a job review that
still needs a decision, a finished Task, a failed Task with its build error, and
a Master budget stop.

| | Light | Dark |
|---|---|---|
| Inbox, 1440px | `inbox-desktop-light.png` | `inbox-desktop-dark.png` |
| Inbox, 390px | `inbox-phone-light.png` | `inbox-phone-dark.png` |
| Header popover, 1440px | `header-desktop-light.png` | `header-desktop-dark.png` |
| Header popover, 390px | `header-phone-light.png` | `header-phone-dark.png` |

## What the run asserts, not just shows

- The header returns unread rows across all three sources (`job_review`,
  `task_outcome`, `master_budget`) with `count = 4`.
- The Inbox entry for the failed Task carries its build error verbatim.
- `POST /api/attention/{id}/dismiss` on the navigate-only Master budget item
  succeeds, the item leaves the header, and the Inbox copy is still there and
  still `open` (#157).
- Page console clean on every capture.

## Two phone-width bugs the capture exposed

Both pre-dated this work and were invisible because the sheet they affect was
never actually visible:

1. `.header-status-cluster` carried a `backdrop-filter` at phone width. A
   filtered ancestor becomes the containing block for `position: fixed`, so the
   notifications sheet resolved to the cluster's own width - **20px**. The
   cluster is a grid cell in the bar's own row there, so nothing sits behind it
   to blur; the filter is gone.
2. With the sheet finally full width, it painted *behind* the main pane and the
   tool rail, because the cluster is a grid cell that paints before both. It now
   carries `--z-attention-popover`, under toasts and above the Master trigger.

The dark theme's `--ui-danger-bg` is still not overridden per theme (flagged in
#154), so the attention trigger's active state rendered as a near-white disc on
the dark surface. That one control now mixes its tint from `--ui-danger`, which
is theme-correct; the wider audit remains open.
