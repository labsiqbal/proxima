# Security Boundaries

Proxima is currently a **single-user owner cockpit**. The app does not implement
multi-user authorization. The access boundary is the network layer in front of
Proxima.

## Boundary Model

```text
Server/operator boundary: physical access, SSH, AnyDesk, sudo, filesystem access
Network access gate: loopback, Tailscale, Cloudflare Access, or equivalent
Proxima app boundary: one owner, projects, profiles, sessions, files, terminal
Runner boundary: selected runner subprocess, selected profile home, project cwd
```

Anyone who can reach the Proxima API should be treated as the owner. Do not
expose it directly to the public internet.

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

## Command Policy

The command policy classifier blocks obvious global/system install operations and
remote-script patterns. Treat it as a guardrail, not a sandbox.

Blocked examples:

```text
sudo apt install ...
npm install -g ...
pip install --user ...
pip install --break-system-packages ...
curl ... | bash
```

Project-local dependency commands may be allowed when cwd stays inside the
project root.

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

## Future Secure Mode

If Proxima ever supports untrusted users, it needs a separate secure mode:

```text
real app auth and roles
OS/container isolation per user or workspace
runner sandboxing
resource limits
secret redaction
audited break-glass workflows
```

Until then, document deployments as single-owner only.
