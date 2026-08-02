# Master persistence migration

Migration 31 converts the former Alpha orchestrator identity to the
product-native Master without creating new history or parallel ledgers. It runs
unconditionally at startup.

## Durable mapping

| Before | Canonical state | Preservation rule |
| --- | --- | --- |
| `profiles.system_kind='alpha'` | `profiles.system_kind='master'` | same profile primary key and managed runner home |
| `sessions.mode='alpha'` | `sessions.mode='master'` | same project-unbound session primary key |
| `jobs.alpha_session_id` | `jobs.origin_master_session_id` | same values, `sessions(id)` foreign key, `ON DELETE SET NULL`, and claim index |
| `alpha.*` app settings | `master.*` | same values; conflicting old/new values refuse migration |
| Alpha run kinds, attention kinds, audit actions, and owned payload keys | Master names | row primary keys and ownership links do not change |

Messages, runs, events, checkpoints, turn journals, budget counters, attention,
Task delegations, Task dependencies, and Task worker sessions stay in their
existing tables. `jobs` remains Task truth. Migration 33 adds
`master_projections`, an idempotency and source-link ledger for concise Master
messages and session events. It does not copy or replace lifecycle state.

Migration 33 validates more than the presence of column names. On a complete
application schema it requires the canonical constraints, restrictive
message/event foreign keys, owner/source uniqueness indexes, valid source-to-event
type mappings, complete message and event links, bounded matching JSON payloads,
and a project-unbound owner-matched Master session. A partially created or
malformed projection table is ambiguous and fails closed without advancing schema
version 32. Intentionally minimal old test or bootstrap schemas that do not yet
contain the application backbone remain eligible for the earlier migration chain.

Migration 38 adds `master_focus_epochs`, `master_focus_state`, `message_focus`,
and `runs.focus_epoch_id`. Legacy Master messages are explicitly attributed to
Fleet rather than assigned to invented Container epochs. Migration 39 adds an
explicit pending-presence discriminator, recovers a pending Fleet request from the
version gap left by schema 38, removes the Container foreign key from historical
epochs, and installs the shared Master run-message attribution triggers. Container
deletion can therefore close the live Focus while preserving the original epoch
identity.
Migration 40 rejects generic or mismatched Master runs at persistence, copies a
Master Task's captured epoch onto `task_delegations`, and makes that copy
immutable. Existing delegation rows are backfilled only when their linked origin
message still proves its Focus; incomplete legacy origins remain executable after
normal scoped ownership validation but unprojectable rather than being guessed.
Migration 41 makes captured message and run Focus epoch ids immutable at the
database boundary without rewriting existing attribution.
Migration 42 freezes the remaining history scope. It removes destructive
Container and Area foreign keys from message Focus and routing context, backfills
recoverable Focus, subject, and explicit-target Container ids, copies that
attribution into existing projection/event payloads, and rejects later edits to
message Focus, subject, target, or Area attribution. Container deletion therefore
cannot silently move historical messages into Fleet history.

Checkpoint and job-input payloads are rewritten only for known ownership keys:
`alpha_session_id` becomes `origin_master_session_id` and
`alpha_dispatched` becomes `master_dispatched`. User message and prompt text are
not rewritten. Job and checkpoint payload rewrites are limited to Master-owned
jobs, attention rewrites to legacy orchestrator kinds and sources, event rewrites
to the Master session or its owned jobs, and audit rewrites to `alpha.*` actions.
Unrelated business data containing words or identifiers such as `Alpha` or
`alpha` is preserved byte-for-byte.

## Startup invariants

For each owner, startup accepts zero or one Alpha/Master system profile and zero
or one Alpha/Master session. A session must be project-unbound and linked to that
one system profile. Migration refuses rather than guesses when:

- Alpha and Master identities both exist for one owner
- a system session is project-bound or linked to a different profile
- old and new job-origin columns contain different non-null values
- old and new settings disagree
- a Master-origin job points at a non-Master session
- an owned compatibility payload is malformed or contains conflicting keys
- SQLite reports a foreign-key violation

Migration 31 is transactional. A refusal rolls back the whole migration and does
not record the schema version. The standard pre-migration `VACUUM INTO` backup
remains available for operator recovery.

The first authenticated Master entry point creates exactly one hidden Master
profile and one project-unbound Master session.
Repeated startup, migration re-run, runner switch, and partial identical-column
recovery reuse those primary keys.

## Compatibility release

Canonical APIs are `/api/master/desk`, `/api/master/messages`, and
`/api/settings/master`. Canonical responses use Master names and
`origin_master_session_id`.

For one release, authenticated deprecated aliases remain at `/api/alpha/desk`,
`/api/alpha/messages`, and `/api/settings/alpha`. The desk alias projects the
same rows with legacy mode, run, and origin field names. Legacy payload readers
also accept the former turn-restore acknowledgement and stored desk keys.

The compatibility aliases do not create an Alpha identity or duplicate rows.
They are governed by the same authorization checks as the canonical routes. `alpha_supervisor.py` is an import-only alias to the one
`MasterSupervisor`; it does not host another loop. New Task and Satpam projections
append to the same migrated session and preserve the ordering and primary keys of
the Alpha-era messages already there.

## Recovery matrix

| Database state | Result |
| --- | --- |
| Fresh current schema, feature off | preserve zero Master identities |
| Fresh current schema, feature on and first Master use | provision one Master identity |
| Current Alpha schema | rename identity and origin column in place |
| Pre-current schema | apply all earlier migrations, then migration 31 |
| Canonical identity and column already present | no-op validation |
| Both origin columns with identical or one-sided values | coalesce, drop legacy column, rebuild canonical index |
| Both origin columns with conflicting values | refuse and roll back |
| Mixed Alpha/Master profile and session names for the same linked identity | normalize in place |
| Dual identities, wrong profile link, or project-bound system session | refuse and roll back |
| Complete schema with no projection table | create the strict projection ledger and indexes |
| Valid migration 33 schema and rows | validate and reuse without rewriting history |
| Partial table, incomplete links, mismatched source/type, or malformed payload | refuse and leave migration 33 unapplied |
| Schema 38 pending Container request | preserve it with the explicit pending marker |
| Schema 38 pending Fleet request | recover it from the state/epoch version gap |
| Container deletion after migration 39 | preserve the epoch's immutable numeric Container identity |
| Task delegation with a surviving attributed origin message | copy its epoch during migration 40 |
| Task delegation whose legacy origin attribution is missing | preserve the Task lifecycle but fail closed on a new projection |
| Schema 40 message and run Focus attribution | preserve existing values and install the migration 41 immutability triggers |
| Schema 41 Master message scope | preserve recoverable Focus, subject, and explicit-target ids; synchronize projection/event attribution; install the migration 42 immutability triggers |
| Container deletion after migration 42 | retain historical message Focus, subject, target, and Area ids for stable history projection |

Feature-off startup still migrates and validates persistence, but does not
instantiate the Master supervisor or projection service, resume committed Master
delegation start intents, or claim Master and Master-owned Task runs. Enabling the
flag later resumes the same queued rows and identity.
