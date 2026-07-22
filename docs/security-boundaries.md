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
links `$HOME` or another broad root as a project, the runner can operate there.
Agent subprocesses no longer inherit the entire service environment: platform basics
and common provider credentials are passed, unrelated Proxima/Cloudflare/update secrets
are omitted, and extra variables require `PROXIMA_RUNNER_ENV_ALLOWLIST`. This reduces
credential leakage but does not prevent the process reading files available to its OS user.

Tool permission requests ask the owner by default. Auto-approve remains available as
an explicit trusted-owner setting and is recorded in run events.

## Script steps (hash-bound trust, honest statement)

A plan's `script` node executes a file from the project's `scripts/` folder as the
service OS user, with the project container as cwd and a minimal environment
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
past the owner; approving without reading the script waives that control.

## Filesystem Rules

Project file APIs must be rooted in the project path from the database. Client
input must be relative and normalized.

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
both HTTP access logs and WebSocket/error logs before they reach the journal.

## Project app preview

Run & Preview remains an explicit owner-power action. Its subprocess receives a
filtered environment (additional names require `PROXIMA_APP_ENV_ALLOWLIST`) but runs as
the service OS user. Preview transport is isolated from owner credentials: local direct
preview switches between `localhost` and `127.0.0.1`, remote preview uses a short-lived
preview-only capability, reverse proxies strip Cookie/Authorization and upstream
`Set-Cookie`, and same-origin generated HTML is rendered without `allow-same-origin`.

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
