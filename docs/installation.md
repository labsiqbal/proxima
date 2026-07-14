# Proxima Installation

Proxima is currently a **single-user cockpit**. It auto-creates one owner and the
frontend signs in through `/auth/auto`; there is no login, bootstrap, invite, or
team-user flow.

## Requirements

- Linux/macOS/Windows host
- `uv`
- Node.js + `npm`
- At least one authenticated agent CLI: Claude Code, Codex, Gemini CLI, or Hermes

Proxima ships no provider credentials. It uses the runner CLIs already installed
and authenticated on the host.

## Linux user install

From the repo root:

```bash
bash scripts/install-user
```

Preview the same install without building dependencies, writing config or unit
files, changing symlinks, or calling `systemctl`/`loginctl`:

```bash
bash scripts/install-user --dry-run
```

The installer:

- syncs backend dependencies with `uv`
- installs web dependencies and builds the PWA
- writes `~/.config/proxima/proxima.env`
- installs a systemd user service named `proxima`
- installs a daily backup timer

Open the configured local URL after install. The default packaged bind is
`127.0.0.1:8765`; the current live handoff may use a different port.

For a system-wide Linux service under `/opt`, `/etc`, and `/var/lib`, follow the
complete manual procedure in [`infra/systemd/README.md`](../infra/systemd/README.md).
`scripts/install-local` only copies/builds the application and creates the CLI and
config; it does not create a service account or install/enable systemd units, so it
is not a complete managed-service installer.

## macOS install

```bash
bash scripts/install-macos
```

This creates the `com.minarflow.proxima` LaunchAgent, writes
`~/.config/proxima/proxima.env`, links `proxima` into `~/.local/bin`, and writes
logs to `~/Library/Logs/proxima.log`. It does not install an automatic backup
schedule; see [backup.md](backup.md).

## Windows install

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

This registers the `Proxima` Scheduled Task and writes runtime/config/log files
under `%LOCALAPPDATA%\proxima`. Re-running the installer rebuilds the app and
replaces the task without overwriting `proxima.env`. Windows backups are manual or
scheduled separately; see [backup.md](backup.md).

## Runtime Paths

Default user-local paths:

```text
~/.config/proxima/proxima.env
~/.local/share/proxima/proxima.db
~/.local/share/proxima/workspace
~/.local/share/proxima/hermes-profiles/<owner>/<profile>
~/.local/share/proxima/backups
```

Runtime data stays outside the repository.

## Configuration

Common variables in `~/.config/proxima/proxima.env`:

```bash
PROXIMA_HOST=127.0.0.1
PROXIMA_PORT=8765
PROXIMA_DB_PATH=~/.local/share/proxima/proxima.db
PROXIMA_WORKSPACE_ROOT=~/.local/share/proxima
PROXIMA_HERMES_PROFILES_ROOT=~/.local/share/proxima/hermes-profiles
PROXIMA_LINK_ROOTS=$HOME
PROXIMA_DEFAULT_RUNNER=claude-code
PROXIMA_CLAUDE_LIVE_HOME=0
PROXIMA_UPDATE_REPO=labsiqbal/proxima
PROXIMA_SERVICE_NAME=proxima
PROXIMA_FEATURE_VIDEO=0
PROXIMA_FEATURE_DESIGN_STUDIO=0
PROXIMA_FEATURE_WORKFLOW_GRAPH=0
```

Notes:

- `PROXIMA_LINK_ROOTS=$HOME` lets the owner link any folder under home as a project.
- `PROXIMA_CLAUDE_LIVE_HOME=1` makes the Claude Code runner use the live
  `~/.claude` home. This is powerful and broad; the current handoff says whether
  it is enabled.
- Video and Design Studio are temporarily disabled by default. Image generation
  remains available without Studio bridge actions.
- `PROXIMA_FEATURE_WORKFLOW_GRAPH=0` keeps the graph architect, navigation, routes,
  and worker inert. Set it to `1` and restart to use the reviewable DAG canvas; the
  classic linear engine is unaffected either way. See [workflow-graph.md](workflow-graph.md).

Restart after backend/config changes:

```bash
systemctl --user restart proxima
```

After web changes:

```bash
npm --prefix apps/web run build
systemctl --user restart proxima
```

Depending on the deployed static serving setup, built assets may be served
immediately; hard-refresh or use an incognito window if a service worker is stale.

## Updating

Proxima checks GitHub Releases for a newer version every 6 hours (and on
Settings → "Check for updates"). When one exists, the sidebar shows an update
pill; it opens the release notes with a one-click **Update now** button
(Linux/macOS). The update runs `git pull --ff-only`, rebuilds, restarts the
service, and the UI reloads on the new version. Failures leave the old
version running; the log lives at `~/.local/share/proxima/update.log`.

CLI equivalent (also the Windows path):

```bash
proxima update
```

**Privacy:** the check is a single unauthenticated HTTPS request to
`api.github.com` every 6 hours; it sends nothing beyond the request itself.
Disable it (or point forks elsewhere) in `proxima.env`:

```bash
PROXIMA_UPDATE_CHECK=0
PROXIMA_UPDATE_REPO=your-account/your-fork
```

The one-click updater requires the Proxima process to own a writable checkout and
build artifacts and to be allowed to restart its service. The recommended
root-owned system-wide deployment deliberately does not grant those permissions:
it disables the periodic check and is updated by an administrator. User installs
retain the self-update path.

## Development

```bash
bash scripts/dev
```

This runs an isolated dev DB and starts:

- API on `127.0.0.1:8765`
- Vite dev server on `127.0.0.1:5177`

Verification:

```bash
cd apps/api && uv run python -m pytest -q
cd apps/web && npx tsc --noEmit && npm run build
```

Use `python -m pytest`; plain `uv run pytest` is known to fail to spawn in this
environment.

## Production and staging

Production and staging never share a checkout, database, config, service, or
public hostname:

| Profile | Service | Config | Data root | Port | Hostname |
| --- | --- | --- | --- | --- | --- |
| Production | `proxima.service` | `~/.config/proxima/proxima.env` | `~/.local/share/proxima` | `8765` | `proxima.minarflow.com` |
| Staging | `proxima-staging.service` | `~/.config/proxima-staging/proxima.env` | `~/.local/share/proxima-staging` | `8767` | `proxima-staging.minarflow.com` |

System-wide deployments use the equivalent `/etc`, `/var/lib`, and `/opt` roots
documented in `infra/systemd/README.md`. Both hostnames must stay behind
Cloudflare Access (or an equivalent owner-only gate), and each service remains
bound to loopback. Proxima is a fresh install; data from any earlier product is
not imported.

The staging config sets `PROXIMA_SERVICE_NAME=proxima-staging`; the CLI reads this
before restart/status/log operations so a staging checkout cannot target the
production unit by default.

## Remote Access

Proxima should not be exposed directly to the public internet. The app assumes
that whoever reaches it is the owner.

Use one of:

- loopback only
- Tailscale / Tailnet
- Cloudflare Access in front of the local service
- another equivalent access gate

Tailscale Serve example:

```bash
sudo tailscale set --operator=$(whoami)
tailscale serve --bg 8765
tailscale serve status
```

Cloudflare Access deployments should route to the local bind address and restrict
access before traffic reaches Proxima.

Full step-by-step guides for both paths (Tailscale; Cloudflare Tunnel + your own
domain + Access) are built into the app: **Settings → Remote Access**.

## Backups

The user install creates a daily backup timer. Manual backup:

```bash
bash scripts/backup
```

Backups are stored under `~/.local/share/proxima/backups` by default.

## Troubleshooting

- UI stale after web changes: run `npm --prefix apps/web run build`, restart if
  needed, then hard-refresh.
- Runner unavailable: confirm the selected CLI is installed, authenticated, and on
  `PATH`; restart `proxima`.
- Need a fresh local DB: stop the service, move/delete the DB under
  `~/.local/share/proxima/`, then restart.
- Need current live details: read [STATUS.md](STATUS.md), not old transcripts.
