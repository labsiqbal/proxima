# Master persistence migration

Migration 31 converts the former Alpha orchestrator identity to the
product-native Master without creating new history or parallel ledgers. It runs
at startup regardless of `feature_master_orchestrator`.

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
existing tables. `jobs` remains Task truth. No Master-specific copies of those
ledgers exist.

Checkpoint and job-input payloads are rewritten only for known ownership keys:
`alpha_session_id` becomes `origin_master_session_id` and
`alpha_dispatched` becomes `master_dispatched`. User message and prompt text are
not rewritten.

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

Fresh provisioning creates exactly one hidden Master profile and one
project-unbound Master session after the owner and default worker profile exist.
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
They are governed by the same server feature flag and authorization checks as
the canonical routes.

## Recovery matrix

| Database state | Result |
| --- | --- |
| Fresh current schema | provision one Master identity |
| Current Alpha schema | rename identity and origin column in place |
| Pre-current schema | apply all earlier migrations, then migration 31 |
| Canonical identity and column already present | no-op validation |
| Both origin columns with identical or one-sided values | coalesce, drop legacy column, rebuild canonical index |
| Both origin columns with conflicting values | refuse and roll back |
| Mixed Alpha/Master profile and session names for the same linked identity | normalize in place |
| Dual identities, wrong profile link, or project-bound system session | refuse and roll back |

Feature-off startup still migrates and validates persistence, but does not start
the Master supervisor or claim Master and Master-owned Task runs. Enabling the
flag later resumes the same queued rows and identity.
