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

Remote preview without an apps domain opens one **relay listener per running app**.
The relay's interface is `PROXIMA_PREVIEW_BIND`; the default is `auto`: the Tailscale
interface when the host is on a tailnet, otherwise loopback - never `0.0.0.0`. Tailnet
devices can reach previews out of the box; untrusted plain-LAN devices cannot. The
listener answers 403 without the preview capability and closes with the app; what it
exposes when authorized is the previewed dev server, never the Proxima API or owner
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
