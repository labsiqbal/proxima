# Security Boundaries

Proxima is currently a **single-user owner cockpit**. The app does not implement
multi-user authorization. Its primary access boundary is the network layer in front
of Proxima, with one owner password/session as defense-in-depth.

## Boundary Model

```text
Server/operator boundary: physical access, SSH, AnyDesk, sudo, filesystem access
Network access gate: loopback, Tailscale, Cloudflare Access, or equivalent
Proxima app boundary: one owner, projects, profiles, sessions, files, terminal
Runner guardrails: filtered child env, selected profile home, project cwd, approvals
```

Any authenticated Proxima session has full owner authority. Do not expose the app
directly to the public internet or treat its password gate as tenant isolation.

## Password gate (defense-in-depth)

On first run the owner sets a password (`POST /auth/set-password`). Once set,
every request must carry a valid session — a bearer token or the HttpOnly
`proxima_session` cookie issued by `POST /auth/login` — and passwordless
auto-login (`/auth/auto`) is refused. Database sessions expire after 14 days by
default (configurable) or immediately on logout/password change. This is a second
layer *on top of* the network boundary,
not multi-user authorization: there is still exactly one owner. If the password
is lost, recover locally with `scripts/reset-password` (clears the hash + revokes
sessions; you have machine/DB access, which is the recovery path).

## Server Operators

Anyone with physical access, SSH, AnyDesk, sudo, or direct filesystem access can
inspect source code, runtime data, DB files, runner profile homes, and project
files. This is outside Proxima app control.

## Safe-update boundary

Safe self-update is not an app privilege. When enrolled in a future managed
installation, a root-owned external updater owns the release journal, pointers,
maintenance fence, backups, trust metadata, service configuration, and recovery
authority outside candidate code and runtime data. The app's authenticated
`self_update_runs` record is an owner-visible projection, not the decision record.
Candidate code cannot use it to promote itself. Until the installer qualifies a
manager and sandbox, `feature_safe_self_update` is off and all activation fails
closed. Enrollment must prove that the candidate identity neither owns nor has
mode, group, ACL, or inherited write access to each trusted journal, pointer,
fence, backup, and service-configuration path or any replaceable ancestor. Every
trusted path and ancestor must be owned by root or the controller identity. The
foundation rejects privileged candidate identities, declared service capabilities,
and candidate configurations that allow privilege escalation. Its POSIX access probe
drops to the actual candidate UID, primary GID, and supplementary groups, sets
no-new-privileges on Linux, and rejects retained permitted, effective, or ambient
capabilities, so effective ACL and inherited access participate in the decision.
Release publication consumes an authenticated verified file set and uses pinned
directory descriptors plus fresh controller-owned inodes rather than path-based
reopens, renames, or hardlinks of candidate-owned files. No installer enrolls this
boundary yet. See
[ADR-0008](adr/0008-external-safe-update-authority.md) and the [adapter
playbook](adding-safe-updater-adapter.md).

The candidate gate runs before any live mutation. Candidate-controlled Git checks,
package hooks, tests, builds, documentation generation, migrations, API servers, and
browsers all pass through one Bubblewrap boundary with no host network, no host
privilege, no ambient environment, phase-specific read-only and candidate-local
writable mounts, bounded resources and output, timeouts, and process-group cleanup.
The build uses a disposable writable copy and offline cache. Only the exact verified
post-build tree is copied into fresh controller-owned release inodes and frozen.
SQLite's backup API makes the raw clone; a fixed migration entrypoint can write only
its clone directory, and controller validation checks the complete
`schema_migrations` ledger. The served fixture is fresh migrated schema populated
only with synthetic rows and candidate-local workspace and runner-home paths.

The candidate cannot select migration or probe commands or replace the separately
installed trusted probe bundle, whose complete tree digest is policy-pinned. That
suite starts the frozen candidate in a loopback-only network namespace and exercises
API identity, version, authenticated maintenance, SSE, served assets, the complete
asset digest, and trusted browser scenarios. Candidate-mode startup rejects schema
initialization, migration, and background writers. Evidence contains the fixed logs
and proofs, is frozen at both file and directory levels, and is rehashed against the
journal digest during recovery. A failed build, migration, identity, static asset,
probe, or sandbox check leaves the live database, workspace, runner homes, services,
pointers, fence, and backups untouched. The accepted preflight journal remains
nonterminal, and frozen failure evidence is retained for inspection. This is not
enrollment or activation.

For the Master selector scenario, the controller mounts the policy-pinned probe
bundle's version-only Codex fixture through an explicit read-only auxiliary-tool
boundary. Before candidate server startup, the trusted probe requires its exact
namespace path and version, verifies that it refuses turn invocation, and proves
that candidate code cannot write it. The fixture is candidate evidence only: it
does not carry credentials, signing authority, or production update authority.

Group 16 adds only a disabled transaction fixture. Its controller root must be an
explicitly initialized empty directory beneath the system temporary directory,
its fence uses the canonical `status/fence.json` path, and live-fixture and
staged-fixture database paths are confined to separate role directories. The only
accepted service adapter is the in-memory
`DisposableServiceAdapter`; no system adapter gains enrollment or service-control
authority.

The fixture controller holds the native single-flight lock across switching and
recovery. Before the `last_good_committed` append returns successfully, any
possibly committed database or pointer replacement restores the sealed database
and both prior pointers before the previous fixture service is resumed. A full or
partial journal write whose append does not return is never treated as an
acknowledged phase and latches the breaker after rollback. Once the last-good
append is acknowledged, recovery can only resume the candidate or latch the
breaker. The persisted breaker verdict is checked before journal recovery, so an
unacknowledged but complete journal tail cannot override a physical rollback.
Before the first physical rollback mutation, the controller persists a
`rollback_required` verdict and finalizes that verdict only after database,
pointer, service, writer, and fence restoration finishes. An unreadable journal
or interrupted breaker write leaves a durable pending marker and latches fail
closed. Recovery also reconciles the fixture's owner-bound pending and active
fence state with the journal, so a fence created before `write_fenced` was
acknowledged cannot be reported as a safe candidate discard.

External maintenance activation first publishes a durable pending marker, then
takes an exclusive ingress lock that only the controller provisions. The
application opens that existing lock read-only and never creates or modifies the
controller status directory. Pending state records its activation owner, and a
later controller run cannot adopt or clear an interrupted activation. Startup
initialization, HTTP requests, active agent runs, project-app and preview proxy
requests, deterministic script runs, and terminal WebSocket sessions hold a
shared ingress lease through their last possible side effect. Terminal,
project-app, agent-runner, and deterministic script processes use a PID namespace
while this boundary is configured. Cached runners retain a lifetime lease after a
turn, and pending activation stops and positively verifies those namespaces before
their leases release. Script leases release only after the script namespace exits.
Detached descendants therefore cannot outlive the drain. New ingress fails closed
as soon as activation is pending, while the controller waits for already admitted
operations to drain before publishing the fence. Read endpoints do not
initialize personal wiki or profile state, stop preview relays, create PATH
compatibility shims, launch provider readiness probes, or reap legacy update
processes while fenced. Normal
first-use personal wiki reads still seed `index.md` while holding the ingress
lease. Application SQLite connections configured for an external fence disable
prepared-statement caching, allow an admitted operation to finish its database
effects, and deny writes outside an admission dynamically. Ordinary unconfigured
connections retain statement caching and do not run fence checks for read-only
opcodes. New fenced connections skip write-capable WAL setup.
`POST /auth/resume` remains available because it only projects an
already-authenticated session; session creation, including `/auth/auto`, is fenced.
A process started directly in maintenance mode opens SQLite read-only with
authorizer denial and starts no workers, schedulers, graph lifecycle tasks,
registry refreshers, or Master supervisor. These controls validate fixture
behavior only. They do not activate a production service, replace live data,
switch a live release, grant signing authority, or add remote push.

## App Owner

The single owner can:

- create/link projects
- browse/edit project files
- start app preview commands
- open a browser terminal
- run agent profiles
- access the audit log

This is intentional. It is not safe for untrusted users.

## Runner And Prompt Boundary

Prompts, project files, wiki notes, artifacts, and runner output are untrusted
input. Prompt text cannot grant itself permission.

Agents run with the same OS privileges as the Proxima service user. If the owner
links `$HOME` or another broad root as a Container, a general chat runner can operate
there.
Agent subprocesses no longer inherit the entire service environment: platform basics
and common provider credentials are passed, unrelated Proxima/Cloudflare/update secrets
are omitted, and extra variables require `PROXIMA_RUNNER_ENV_ALLOWLIST`. This reduces
credential leakage but does not prevent the process reading files available to its OS user.

Tool permission requests ask the owner by default. Auto-approve remains available as
an explicit trusted-owner setting and is recorded in run events.

## Task delegation boundary

Scoped Task creation is a server-owned operation. Work, Home quick Task, Master, and
future orchestration callers pass database identities, never filesystem paths, to
`TaskDelegationService`. The service verifies that the authenticated owner owns the
Container, that the one selected Area is active and belongs to that Container, that
the Task-agent profile belongs to the owner and is not a system identity, and that
origin messages belong to their origin session. A Recipe bound to another Container
is rejected.

Creation is transactional and idempotent. The worker session, job, delegation audit,
and dependency edges either all commit or none do. A caller-provided idempotency key
is bound to a fingerprint of the request; replay returns the same Task before mutable
referenced rows are revalidated, while reuse for different input is rejected.
Dependency edges reject duplicates, self-edges, cycles, inaccessible prerequisites,
and prerequisites already failed or cancelled. SQLite cycle triggers protect the same
invariant from non-service writers, and a restrictive prerequisite foreign key prevents
deletion from silently removing a required edge.

Start happens only after that transaction commits. A durable start intent lets restart
recovery retry safely, including reconciliation of a graph Task interrupted after its
`running` claim but before its first node run. Dependency readiness is checked from
live `jobs` state, and unmet or failed prerequisites persist a visible blocked reason
rather than leaving an unexplained queue row. Starting still uses the existing guarded
job claim, worktree, and run queue.

## Master persistence and activation boundary

Master's durable profile, project-unbound session, messages, runs, checkpoints,
budgets, attention, delegations, and Task ownership remain in the existing SQLite
tables. Migration 31 changes the former Alpha identity and origin column in place
and refuses ambiguous identities, conflicting compatibility columns, malformed
owned JSON payloads, or foreign-key violations. It does not create a parallel
ledger or a second Master session.

`feature_master_orchestrator` is server-owned and defaults off. Persistence
migration and identity recovery still run while it is off, but canonical and
deprecated compatibility routes reject use, the supervisor does not start, and
the run worker leaves both Master turns and Master-owned Task runs queued. This
gate limits activation only. It does not weaken migration checks or authorize a
runner.

The temporary `/api/alpha` and `/api/settings/alpha` aliases are authenticated
projections over the same records. They grant no extra access and disappear after
the compatibility release.

Master adds a separate enforceable runner boundary. The centralized runner spec must
declare `master_chat_only=True`, otherwise both runner selection and message creation
return `master_runner_not_conforming` before a turn starts. A conforming adapter gets
one dedicated managed runner home and one empty read-only scratch. It receives no
Container, Area, repo, Ops, Proxima source, runtime data, configuration, ordinary
profile home, path, or bearer material. The stored selection is exactly
`{"skills":[],"mcp":[]}` and strict application on every run prevents null or
omitted capabilities from inheriting detected skills or MCP. Every native permission
request is denied and every runner-native tool event fails the turn. Codex
app-server 0.145.0 or newer proves this contract through empty execution
environments and a loopback provider firewall. The firewall discards Codex's full
tool set, injects only exact server-owned broker schemas, discards runner-generated
developer context, and installs a fixed filesystem-isolated developer policy.
Schema drift fails closed. Its single secret route rejects ambiguous or encoded
requests, redirects, and encoded responses, and buffers a bounded provider
response before releasing it to Codex. Bearer material remains only in the
provider HTTP header. Other production adapters remain unsupported for Master. See
[runner-conformance.md](runner-conformance.md).

Cross-Container isolation continues through prompts, responses, projections, and
history UI. Every Master turn recycles the runner process and rebuilds its transcript
only from the run's captured Focus epoch, so a prior Container's prompt and response
are not supplied to a later Container turn. Server-owned projection summaries omit
source prose, paths, and credentials and carry immutable Focus and subject ids. The
Container and Fleet folders filter the canonical message ids from that attribution;
Fleet excludes every Container-subject update, and deleting a Container cannot erase
its ids and reclassify its history as Fleet. The Roving thread remains the owner's one
complete canonical view. See [master-supervision.md](master-supervision.md).

Master product actions cross only `MasterToolBroker`. Its closed JSON schemas accept
bounded product IDs and text, never paths. The broker resolves owner-scoped IDs in
trusted Proxima code and returns bounded records without absolute host or internal
graph paths. `query_context` is the narrow exception that returns validated
scope-relative source citations. Delegation and start call `TaskDelegationService`,
preserving exact Container/Area binding, dependency validation, atomicity, and
idempotency. A streaming parser, per-turn durable envelope ledger, and
request/result/round/output caps turn malformed, replayed, duplicate, or oversized
calls into visible deterministic errors.

The Master broker is an authority and consistency boundary, while runner conformance
must separately prove the process boundary. Repo Tasks
still run in the existing external worktree and require review before local merge.
Delegated Ops Tasks still receive the physical `ops/` cwd and finish without a landing
review, independently of Guarded or Autonomous in-run permissions. The runner process
for a Task retains the service user's host permissions as described above; a cwd alone
does not prevent `..` traversal or arbitrary host reads.

The Graphify adapter is a separate server-owned filesystem boundary. Its API accepts
only an authenticated Container slug, graph kind, and registered Area id. It never
accepts a shell command, absolute or relative path, Graphify CLI argument, raw MCP
project path, or semantic backend. Knowledge resolves to the physical Ops boundary;
Code resolves to one exact active code Area after symlink resolution and excludes
all nested registered Areas. Source citations are re-resolved and revalidated at
build and query time, then returned only as paths relative to that validated scope.

Build output is confined to a validated `graphify-out` directory inside that scope.
A symlinked output directory, incomplete walk, escaped citation, malformed JSON,
wrong scope, timeout, or killed worker fails before atomic publication and leaves
the prior canonical graph unchanged. Canonical reads use no-follow descriptor
snapshots bounded by `graph_max_bytes`; last-good backup is a bounded streaming
copy with atomic replacement. A fsynced publication journal stores both prior and
replacement digests, while SQLite finalization updates and re-reads graph state in
one transaction. An ambiguous commit is accepted only after the writer is out of
its transaction and an independent read-only SQLite connection plus a bounded
canonical hash both match the replacement digest; otherwise journal and canonical
bytes remain for locked reconciliation. Failure cleanup cannot overwrite a graph
state that has already committed as `fresh` or `queued`. Knowledge traversal
streams directory entries with `os.scandir()` and caps visited entries and
directories before extraction. Public state and events omit internal paths.
Graphify performs only local structural extraction. Semantic model egress defaults
off, Ops content is never sent to a cloud model, and enabling the future egress
switch makes Knowledge rebuild fail closed. Local-only policy is visible in Master
settings (`graph_policy`), graph state `semantic_backend`, rebuild logs, and docs.
Configured cloud credentials alone never enable egress.

Code graph lifecycle (Group 10) never promotes a Task worktree graph as canonical.
Rebuilds and audits only touch registered Area roots for that Container. Repo
Task-agents may receive a server-managed Graphify MCP entry locked to exactly their
selected Area; the proxy strips or ignores arbitrary `project_path`, so a prompt
cannot retarget another Area's graph through MCP parameters. Master runs do not
receive this MCP entry. Graph absence or rebuild failure never blocks Task
execution or SQLite Live state reads.

Knowledge graph lifecycle (Group 11) never reads outside the resolved Ops allowlist
for that Container. Secret-like names, symlinks, nested repositories, graph
outputs, Task transcripts, and runtime data are rejected before extraction. An
active Container root nested beneath a selected graph scope is also excluded. The
same transaction that completes an Ops Task writes only the affected Container's
durable rebuild intent; filesystem discovery and rebuild remain asynchronous. The
Master context router never merges fleet-wide graphs; focused Knowledge/Code
results are scope-checked so another Container's graph nodes cannot appear. "What
is running?" always answers from SQLite Live state even when every graph is missing
or stale. Blocked/stuck Live questions include dependency-blocked jobs persisted as
`queued` with a non-null `blocked_reason`, and apply that predicate before limiting
results.

## Script steps (hash-bound trust, honest statement)

A plan's `script` node executes a file from the Container's `ops/scripts/` folder as
the service OS user, with the physical Ops Area as cwd and a minimal environment
(`PATH`/`HOME`/locale — never the server's config/secrets env). Execution uses an
exec array, never a shell string, so node args cannot shell-inject; the script path
is jailed to `scripts/` at plan validation and again at resolution (no `..`,
absolute paths, or symlink escapes).

The control is an **approval gate, not a sandbox**: a script's first run — or any
run after its content changed — blocks until the owner approves the exact bytes
(sha256 recorded in `script_trust`; approvals land in the audit log and the step's
timeline). An unchanged approved script then runs without per-run prompts, and it
can do anything the service user can. Because agents write these scripts, the
approval moment is the place where a prompt-injected script body would have to get
past the owner. The approval surface holds that line mechanically (audit F4): the
card shows the script's actual content + sha256 (read together), the approve request
must echo that hash (409 if the file changed after review), and the run executes the
hashed bytes from a private temp copy taken at hash time — so neither an
edit-before-click nor a swap-after-hash can run content the owner never saw.
When the disabled external maintenance boundary is configured, a PID namespace
ensures the script and any detached descendants exit before its ingress lease
releases. This process-lifetime control does not reduce the script's filesystem or
service-user authority and does not turn approval into a general sandbox.

## Push after merge (pinned target, hardened invocation)

When a code area opts into push-after-merge, the remote URL is pinned at opt-in
(`project_areas.push_remote_url`). The repo's own `.git/config` is agent-writable,
so at push time the config's current URL must still match the pin or the push
refuses (audit F3 — blocks `git remote set-url` exfiltration; re-enabling the
toggle approves a new URL). The push runs as an exec array with
`-c credential.helper= -c core.hooksPath=/dev/null` plus `GIT_TERMINAL_PROMPT=0`
and ssh BatchMode, so credential helpers or pre-push hooks planted in repo config
cannot execute in the API process; auth stays the host's ambient ssh.

## Filesystem Rules

Container file APIs resolve paths through the database-selected Container or Ops
Area. Client input must be relative and normalized. Every active Area is realpath
checked to remain inside its Container, and every actual file access is realpath
jailed, so a symlink under `ops/` can never read or write outside the Container.
Duplicate roots, unsafe overlaps, path escape, and a symlinked Container or Ops
root fail closed on every resolution. The full recursive scan that rejects every
symlink beneath physical `ops/` runs at the fail-closed boundaries - Ops creation,
legacy migration, Area mutation, and Area-sensitive execution - rather than on hot
read paths (project lists, Home, file resolution), which stay O(1). Historical
virtual Ops paths remain stable but resolve to the active Ops row, which may
temporarily be legacy `.` while a collision awaits owner attention.

Never allow:

```text
../../..
absolute paths
symlink escape without validation
client-supplied project root
```

Runtime/config/profile directories are not normal project files unless the owner
explicitly links them. Do not add UI that casually exposes raw secrets, tokens,
`.env` files, cookies, or provider auth files.

## Session Tokens And Logs

Browser SSE and WebSocket endpoints accept the HttpOnly `proxima_session` cookie.
The legacy `?token=` fallback remains available for compatible clients, but new
clients should use the cookie because URL credentials can be observed by proxies
and diagnostics. Proxima's Uvicorn configuration redacts `token` query values from
both HTTP access logs and WebSocket/error logs before they reach the journal. Canonical
file-preview capability values are likewise redacted in query strings, retired
gateway paths, and capability cookies. The filter is installed for configured
launchers and plain `uvicorn proxima_api.main:app` startup.

## Canonical file preview

Canonical file previews bind a short-lived capability to one validated Area and the
authenticated Proxima frame origin. Named local and apps-domain deployments use an
Area-only origin. Plain HTTP remote deployments use an Area-only relay. HTTPS remote
deployments require a TLS-capable Area-only hostname under the configured apps
domain. Without one, active preview entry fails with 503 rather than sharing the
Proxima origin or using a plaintext relay. Passive canonical media remains on the
authenticated route with an exact framing policy. TLS exchange uses a Secure,
host-scoped `SameSite=None` capability cookie so Tailscale and apps-domain origins
can remain distinct; the capability's signed Proxima origin is the exact permitted
frame ancestor. HTTP same-site relays retain `SameSite=Strict`. The dedicated
origin lets native
module workers use same-origin Area URLs without gaining Proxima authority.

Legacy active files never execute on the Proxima origin. HTML is upgraded to the
canonical response sandbox, active XML and SVG download, main-origin HTML denies
framing, worker responses restrict connections, and Service Worker scripts are
rejected. Fetch Metadata and opaque-origin checks reject embedded requests that try
to leave the preview boundary for Proxima routes. Same-Area resources still cross
the canonical resolver and realpath jail. Every document-viewable response,
including PDF, receives the exact authenticated frame-ancestor policy.
Design Studio obtains targeted canvas and export pixels through authenticated raw
bytes and temporary blob URLs rather than through preview-origin CORS.

## Project app preview

Run & Preview remains an explicit owner-power action. Its subprocess receives a
filtered environment (additional names require `PROXIMA_APP_ENV_ALLOWLIST`) but runs as
the service OS user. Preview transport is isolated from owner credentials: local and
remote previews use a short-lived preview-only capability, reverse proxies strip
Cookie/Authorization and upstream `Set-Cookie`, and same-origin generated HTML is
rendered without `allow-same-origin`.

The requested dev-server port is never a preview authority. It is a candidate until
procfs maps every listening socket back to the managed process group. Appview, relay,
and subdomain paths then open an upstream connection and map its server-side socket
back to that managed group before sending HTTP or WebSocket bytes.
A pre-existing listener produces a structured port conflict before spawn. A listener
that wins after preflight produces the same sticky terminal conflict and only the
managed process group is signaled. Starting, conflict, ownership-unknown, and exited
states have no proxy target, so requests receive a non-proxy response and foreign
content is never sampled. Existing relays remain safe HTTP 503 responders through
terminal states until Stop releases them.

This proof deliberately fails closed. Hosts without usable procfs, incomplete
socket-owner visibility, and uncontained descendants that detach into another process
group report `ownership_unknown`; their listener is not previewed. Each launch receives
an ephemeral lineage marker so a detached owner remains identifiable after reparenting
without becoming trusted. For a contained launch, every socket owner must carry that
marker, match the exact launch-specific PID namespace identity reported by Bubblewrap,
and retain positive live process-group or ancestry evidence to the managed leader.
Marker and namespace evidence without live lineage remains ownership-unknown. Proxima
registers the provisional process and begins output draining immediately after spawn
while namespace proof completes asynchronously; readiness stays fail closed until it
completes.
Provisional cleanup belongs to AppManager rather than the start request. Cancellation
can return immediately, while the manager-owned task completes the in-flight spawn and
reaps only the process Proxima created. A monotonic per-project generation is written
durably before broker creation or process spawn. Atomic pending, broker-attached, and
app-attached phases let restart recover only exact authority. A retry waits for the
matching cancelled generation to settle, and stale cleanup cannot replace or remove
newer authority. Startup and shutdown reconcile project generations concurrently under
fixed aggregate deadlines.

A preview supervisor launches the app and owns its child pipe. It keeps a
bounded complete-line ring and separately bounded partial-line tail, drains all
currently available bytes before returning an atomic final snapshot, and continues
discarding detached output until EOF after the API disconnects. It stays available
until the launch-specific app cgroup is empty. Routine polling uses
versioned line deltas; only explicit finalization requests the full bounded snapshot.
Packaged Linux services obtain profile-specific supervisors from socket-activated
systemd units outside the API service cgroup. Each supervisor creates a delegated,
launch-specific child cgroup and moves the app into it before owner code executes.
The broker remains in the unit root and unit teardown signals only that broker
process. Processes still proven inside the app cgroup are managed; a process that
escapes it remains untrusted and is not signaled. Production and staging have
different sockets, protocol identities, state roots, and executables. The API service
uses `KillMode=process` and a declared stop timeout, while app generations stop
concurrently. If the API restarts before cleanup finishes, it adopts only an exact
durable supervisor, process, app cgroup, profile, protocol, and lineage proof.
Before replacing supervision units, update procedures scan same-user procfs state.
Older protocol markers and pre-protocol preview port environments combined with API
lineage or service-cgroup membership refuse the migration until those previews stop.
Incomplete proof remains
`ownership_unknown` and is neither proxied nor signaled. Windows uses a detached
breakaway supervisor when supported. If durable ownership cannot be established,
the launch transaction is rolled back and status reports the recoverable
`output_sink_unavailable` reason. This policy preserves the ownership boundary
instead of treating a successful TCP handshake as ownership evidence. See
[ADR-0016](adr/0016-live-containment-lineage-gates-preview-authority.md) and
[ADR-0024](adr/0024-preview-generations-use-durable-launch-phases.md) through
[ADR-0026](adr/0026-preview-supervision-upgrades-require-a-drained-legacy-generation.md).

Preview without an apps domain opens one **relay listener per running app**.
The relay's interface is `PROXIMA_PREVIEW_BIND`; the default is `auto`: the Tailscale
interface and loopback share one port when the host is on a tailnet, otherwise it binds
loopback only - never `0.0.0.0`. Local and tailnet devices can reach previews out of
the box; untrusted plain-LAN devices cannot. The
listener answers 403 without the preview capability and 503 without an
ownership-verified ready target; what it exposes when authorized and ready is the
previewed dev server, never the Proxima API or owner
session. Operators may set an explicit interface instead - including `0.0.0.0`, which
deliberately exposes the relay ports to every device on the LAN - or `127.0.0.1`/`off`
for strict loopback-only installs. If no tailnet address is found, `auto` falls back to
loopback, never to `0.0.0.0`.

**The relay only protects its own port.** The dev server it fronts is a separate
listener whose bind address is dictated by the launch command. A preview command that
binds a non-loopback address (`0.0.0.0`, a LAN IP, ...) is directly LAN/tailnet-reachable
with **no authentication** - the relay does not and cannot protect it. For a static file
server or a debug-mode web app that means the whole project tree (including `.env`) is
readable by any device on the network, and a framework debug console can escalate to
code execution. Proxima therefore suggests loopback-bound commands
(`--bind 127.0.0.1` / `runserver 127.0.0.1:$PORT`), sets `HOST=127.0.0.1` for dev
servers that honor it, and the app runner shows a warning whenever a running preview's
port is found listening beyond loopback. Loopback-bound dev servers still preview fine
remotely: the relay always connects to `127.0.0.1:<port>`.

There is no command classifier presented as a security boundary. The owner confirmation,
environment filtering, project cwd, preview credential isolation, and optional OS-level
service separation are the current pragmatic controls.

## Remote Access

Safe deployment options:

- loopback only
- Tailscale/Tailnet
- Cloudflare Access in front of the local service
- equivalent authenticated private access layer

Unsafe:

- binding Proxima publicly without an external access gate
- letting untrusted people reach the API
- claiming app-level isolation protects separate users

## If untrusted-user isolation is ever required

If Proxima ever supports untrusted users, it needs a separate secure mode:

```text
real app auth and roles
OS/container isolation per user or workspace
runner sandboxing
resource limits
secret redaction
audited break-glass workflows
```

Those controls are intentionally out of scope for the normal single-owner self-hosted
path. Until then, document deployments as single-owner only and treat linked projects,
runner skills, and MCP servers as owner-trusted inputs.
