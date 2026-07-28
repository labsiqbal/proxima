# Master supervision and durable projections

Master supervision has two separate authorities:

- `MasterSupervisor` may start eligible queued Master Tasks while unattended mode
  is enabled and capacity and budget remain.
- Satpam alone detects stuck work, steers it, restarts it, or escalates it.

There is no Master progress detector or recovery loop.

## Queue-start contract

The supervisor is instantiated only when `feature_master_orchestrator` is enabled.
The flag defaults off. A defensive feature check also makes a manually retained
instance inert after the flag is disabled.

Each tick:

1. Resolves the canonical project-unbound `sessions.mode='master'` row.
2. Enforces the configured `master_max_parallel`
   (`PROXIMA_MASTER_MAX_PARALLEL`, default 3) active-run limit and unattended turn
   and wall-clock budgets.
3. Reads only queued Tasks whose `created_by` matches that Master session owner.
4. Calls `TaskDelegationService.start`, which revalidates the project-unbound
   Master session, owner, active Container, exact Area, worker session, Task-agent
   profile, and delegation audit before any claim.
5. Lets the Task service own dependency readiness, worktree policy, and the
   transactional queued-to-running claim. A running job without an active run is
   counted as a start reservation until its run is committed or recovery resumes it.
6. Reserves the unattended turn counter in the same immediate transaction as the
   job claim. Separate server processes therefore cannot spend one turn twice.
7. Skips blocked Tasks without consuming a start slot, so an earlier blocked row
   cannot starve later eligible work.

Failed or cancelled prerequisites leave the dependent queued with a durable
`jobs.blocked_reason`. Cycles are rejected by the delegation service and SQLite
triggers before a supervisor tick can see them. The process-local nonblocking tick
mutex reduces duplicate work, while SQLite immediate transactions, conditional
claims, and capacity reservations provide the cross-process safety boundary. Linear
and graph starts use the same global Master capacity. Worker run claims use the same
transactional compare-and-set boundary and revalidate Master ownership and
dependency readiness before accepting a queued legacy run.

## Projection boundary

`MasterProjectionService` is the only writer for asynchronous Task and supervision
messages in the durable Master thread. It reads existing authoritative rows:

- `jobs`, `runs`, `node_states`, `job_checkpoints`
- `attention_items`
- `satpam_interventions`

It writes:

- one concise `messages` row authored by Master
- one named event on the existing Master session stream
- one `master_projections` idempotency/link row

`master_projections` is not lifecycle truth. Its unique owner and projection key
links a message and event to a source table and row. Source table and event type
must match, links are owner-scoped, and committed rows must have both their message
and event. Message, event, and ledger creation is one transaction, so rollback
cannot leave partial projection state. Message and event deletion is restricted;
Task deletion clears the optional Task link while preserving the historical source
identity.

Startup validates the projection table schema, indexes, foreign keys, Master
session ownership, source links, and bounded payload equality. Reconciliation after
restart can safely retry because an existing key produces no second message or
event. A reused key with different ownership or source binding fails closed. Raw
token, reasoning, and tool delta events are never projected, and payloads are
limited to 16 KiB. Projection messages are also server-owned summaries: Task
titles, runner errors, permission commands, Attention text, Satpam reasons, paths,
and credentials are not copied into the Master conversation or event payload.

Review-ready payloads include the stable Task, Container, Area, and latest
checkpoint ids. Attention and Satpam payloads include their source row ids and a
stable `toast_key`. The authenticated shared frontend provider now updates the
durable thread and work panel from these events without another polling endpoint.
Transient toast presentation remains a later UI group and is intentionally inert.

## Session event contract

Task events:

- `master.task.started`
- `master.task.review_ready`
- `master.task.completed`
- `master.task.failed`
- `master.task.cancelled`
- `master.task.blocked`

Supervision events:

- `master.attention.required`
- `master.supervisor.outcome`
- `master.satpam.steered`
- `master.satpam.restart_queued`
- `master.satpam.restarted`
- `master.satpam.recovery_failed`
- `master.satpam.escalated`

Focus events:

- `master.focus.changed`

These are named events on
`GET /api/sessions/{master_session_id}/events/stream`. The existing global
`events.id` cursor remains the resume key. The stream accepts either `after_id` or
the browser-standard `Last-Event-ID` header and resumes after the greater valid
cursor. Reconnect with the last received id delivers only later rows; replay from
an earlier cursor returns the same one event per projection and creates no new
durable data. An idle stream emits an immediate SSE comment to flush a healthy
connection before the 15-second keepalive; comments do not alter cursor or replay
semantics.

Events and projected chat messages report state only. They do not approve review,
landing, restart, or Attention gates and are never accepted as control input.

## Frontend ownership

`MasterStateProvider` mounts once around the authenticated `AppShell`. It owns the
canonical desk and session, ordered messages, active Master turn, durable resume
cursor, one typed `EventSource`, reconnect state, unread count, composer draft and
selection, and stable scroll/work-panel state. `MasterConversation`,
`MasterComposer`, and `MasterWorkPanel` are view-only shared consumers used by the
full-page home and prepared for the later popup.

The provider deduplicates event and message ids, preserves server ordering, and
applies only safe final messages and server-owned Master projection summaries. Raw
message, reasoning, and tool delta payloads are never surfaced. Reconciliation
fetches desk/messages/events only after disconnect/reconnect, a sequence gap,
malformed input, or explicit owner retry, and coalesces reconnect storms into a
bounded number of attempts. Bootstrap reads the desk's constant-size durable
`event_cursor` barrier before its final desk/message snapshots and does not fetch
event history. Successful submission returns the canonical persisted user
message, replacing the pending row with its durable id before streamed replies are
ordered. Lifecycle generations and abort controllers ignore late responses, close
replaced streams, and clear all owner-scoped state on token/owner change,
feature-off, logout, onboarding, or update application.

Focus is server-owned state, not a browser preference. Bootstrap reads the
current epoch, pending request, and optimistic version from the desk. The picker
writes through `PUT /api/master/focus`, explicit message targets transition Focus
inside the message transaction, and `master.focus.changed` updates every live
consumer from the durable boundary event. Local storage is used only for
presentation preferences and the independent per-message target picker.

## Compatibility

`alpha_supervisor.py` is a deliberate import alias to `MasterSupervisor` for one
compatibility release. It has no separate loop or state. Migrated Alpha messages
remain in the same session primary key after that session becomes Master, and new
projections append chronologically to that thread.
