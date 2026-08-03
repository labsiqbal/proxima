# ADR-0044: One notification ledger, two surfaces

- Status: Accepted
- Date: 2026-08-03

## Context

Notifications had exactly one surface: the shell's **Attention** badge, backed by
`GET /api/attention`. That surface conflated two different jobs and did neither
well.

- It was the **only** place a notification ever appeared, so nothing could be
  cleared. An item stayed until some other subsystem happened to resolve its row.
  Navigate-only kinds had no resolver at all: `POST /api/attention/{id}/act`
  refuses any durable row that is not `inline_ok`, so the Master budget notice
  ("Master unattended work stopped") sat behind a red badge forever (#157). Worse,
  its `source_key` was blind to the budget cycle, so `INSERT OR IGNORE` swallowed
  every *later* stop - the owner saw the first notice permanently and never heard
  about the next one.
- It carried only work that **needs a decision**. Everything else the system knows
  - a Task finished, a build failed with a diagnostic, a workflow ended - had no
  home at all. A failure was findable only by remembering which Task to open.

The owner asked for a persistent **Inbox** destination and an ephemeral header
that behaves like phone notifications (#158), with the explicit constraint:
*"check what notification/attention storage already exists before designing a new
table - extend, do not fork."*

## Decision drivers

1. One truth. A notification the owner dismissed from the header and the same
   notification read later in the Inbox must be the same row, not two copies that
   drift.
2. Attention already has a lifecycle - resolve, defer, cascade, the
   `master_projections` ledger keyed on `source_table = 'attention_items'`. A
   second store would have to mirror all of it.
3. Most of what the system emits is **reading**, not deciding. That is a
   destination's job, not a popover's.
4. An upgrade must not dump years of finished work into a brand-new Inbox.
5. Six existing producers write `attention_items` with raw SQL. They should not
   all have to learn about the Inbox.

## Options considered

1. **A new `notifications` table beside `attention_items`.** Clean to write,
   permanently expensive: every attention transition would need a matching write
   on the other side, and the two would diverge the first time one path forgot.
   This is the fork the ticket ruled out.
2. **A read-model over the existing `events` table.** `events` is run-scoped,
   cascades with runs and sessions, and is high-volume streaming traffic. It is an
   event *stream*, not an owner-facing ledger; read state has nowhere to live.
3. **Extend `attention_items` into the ledger.** Keep the table, add the axis it
   was missing.

## Decision

**`attention_items` is the one notification ledger. The header and the Inbox are
two views of it.**

The table gains one new axis and the fields a notification needs:

| Column | Meaning |
|---|---|
| `read_at` | *Has the owner seen this?* The header filters on it. |
| `item_key` | The stable public id, one space for native and projected rows. |
| `severity` | `info` / `success` / `warning` / `error` / `action`. |
| `body`, `detail_json` | The diagnosis, and the step that clears it (#133). |
| `requires_action` | Whether this is a decision or just news. |

`status` keeps its existing meaning - *does this still need the owner* - and the
two axes stay independent. That independence is the whole design:

- **Dismissing is seen, not done.** A dismissed item is still `open`, still
  actionable, still in the Inbox with its buttons. The header can afford to be
  ruthless precisely because nothing is lost by clearing it.
- **Settled work stops shouting.** When the system resolves an actionable item
  (review approved, decision resolved, restart run, budget notice gone stale) it
  is marked read too - it has nothing left to ask. Informational rows are never
  auto-read: only the owner decides they have read those.

Three mechanisms make it work without touching the producers:

- **A trigger stamps `item_key`** (`'attention:' || id`) on insert, so the six
  raw-SQL producers are unchanged.
- **Derived items are mirrored.** Job reviews, node-script trust, and satpam
  restarts are computed by the attention route from other tables. They are
  recorded into the ledger under the same `job:` / `script:` / `satpam:` ids the
  route has always exposed, so the Inbox is a strict superset of everything the
  header ever showed, and one id addresses a row in all three places.
- **Task outcomes are pulled, not pushed.** Terminal transitions are projected by
  reading the jobs table rather than by hooking every producer: a pull cannot miss
  a transition that happened while the server was down, `item_key` UNIQUE makes
  replays free, and a watermark pinned at startup keeps pre-Inbox history out of a
  new Inbox.

**Inbox is a destination in both Work and Delegate.** Notifications are global - a
Task that finished, a Master budget stop, a failed workflow - and the header that
emits them already renders in both modes (ADR-0043 set the same precedent for
Artifacts). An Inbox in one mode only would strand half of them behind a mode
switch.

**The stale rule for the Master budget notice** (#157) is: the item clears itself
once Unattended is switched back on, because the sentence it carries no longer
describes reality. It is not cleared merely because Unattended is off - the notice
is *created* by turning it off, so that rule would erase it instantly. Its
`source_key` now names the budget cycle, so a later stop is a new notification.

## Consequences

- `GET /api/attention` changes meaning: unread rows only, `count` = unread. It is
  now a superset of what it used to return (it carries informational rows too).
  The Master desk's own work panel keeps reading the live list, unfiltered.
- Read state is per-row and global. There is one owner, so there is nothing to
  scope it to.
- Retention is still unbounded, as it was for `attention_items`. The Inbox
  paginates with a `before` cursor; a pruning policy is a later decision.
- A notification the *client* raises (the global error toast, #133/prune B4) stays
  client-side and ephemeral. The failures behind it - a Task that failed, a run
  that errored - reach the Inbox through their own records, which is where the
  diagnostic actually lives. Making the browser POST its toasts into the ledger
  would add a write surface for text no server ever verified.
