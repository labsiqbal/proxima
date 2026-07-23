# Proxima Quickstart

## 1. Prerequisite checks

Confirm the build tools are available before you start:

```bash
uv --version
npm --version
```

You also need at least one authenticated agent CLI: Claude Code, Codex, Grok,
Hermes, or Pi. For example, to use Grok:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok login
grok models
```

Proxima installs without a runner, but agents cannot run until one is installed and
logged in. See [the installation guide](docs/installation.md) for runner details.

## 2. Clone and install

```bash
git clone https://github.com/labsiqbal/proxima
cd proxima
bash scripts/install-user
```

The install script:
- Builds the Python backend (via `uv sync`) and the React PWA (`npm install && npm run build`).
- Writes a config file at `~/.config/proxima/proxima.env`.
- Installs and enables a systemd **user** service (`proxima.service`) that starts automatically on login/boot and restarts on crash.
- Installs a daily backup timer (`proxima-backup.timer`) that runs at 03:00.
- Builds Design Studio (enabled by default); image generation and Workflow Graph remain available.

## 3. First run

Open `http://127.0.0.1:8765` in your browser.

Proxima is single-user. It auto-creates the owner, asks you to set a password on
first run, and uses that login/session afterward. There is no invite/team flow.

## 4. Managing the service

```bash
# Check status and recent logs
systemctl --user status proxima

# Tail live logs
journalctl --user -u proxima -f

# Restart after a config change
systemctl --user restart proxima

# Stop the service
systemctl --user stop proxima

# Update to the latest version (pull + rebuild + restart; runs DB migrations)
./scripts/proxima update
```

## 5. Where data lives

All runtime data lives outside the repository. Default paths:

| What | Default path |
|---|---|
| Database | `~/.local/share/proxima/proxima.db` |
| Workspace / project files | `~/.local/share/proxima/` |
| Agent profile homes (legacy directory name) | `~/.local/share/proxima/hermes-profiles/` |
| Config file | `~/.config/proxima/proxima.env` |
| Daily backups | `~/.local/share/proxima/backups/` |

Override any path by editing `~/.config/proxima/proxima.env` and restarting the service.

Key variables:

| Variable | Default | Effect |
|---|---|---|
| `PROXIMA_PORT` | `8765` | Port the API/PWA listens on |
| `PROXIMA_HOST` | `127.0.0.1` | Bind address |
| `PROXIMA_DB_PATH` | `~/.local/share/proxima/proxima.db` | SQLite database file |
| `PROXIMA_WORKSPACE_ROOT` | `~/.local/share/proxima` | Root for project files |
| `PROXIMA_SOURCE_HERMES_HOME` | _(unset)_ | Hermes home to copy credentials from |
| `PROXIMA_HERMES_BIN` | _(unset, uses PATH)_ | Explicit path to the `hermes` binary |
| `PROXIMA_UPDATE_REPO` | `labsiqbal/proxima` | GitHub release source |
| `PROXIMA_SERVICE_NAME` | `proxima` | Managed service selected by the CLI |
| `PROXIMA_FEATURE_DESIGN_STUDIO` | `1` | Set to `0` to disable Design Studio |

## 6. Phone and other devices (Tailscale)

Proxima listens only on `127.0.0.1:8765` by default, so to reach it from your phone,
expose it to your tailnet with Tailscale Serve.

```bash
# one-time: let your user run Serve without sudo (enable MagicDNS + HTTPS
# Certificates in the Tailscale admin console first)
sudo tailscale set --operator=$(whoami)

# expose Proxima to the tailnet (persists across reboots)
tailscale serve --bg 8765
tailscale serve status
```

Open your Tailscale HTTPS MagicDNS URL (`https://<machine>.<tailnet>.ts.net`) on
any device and install the PWA from the browser menu.

## 7. Backups

The database is a single SQLite file. The daily timer backs it up automatically to `~/.local/share/proxima/backups/`. To run a manual backup:

```bash
bash scripts/backup
```

## Troubleshooting

- **Web UI not loading**: run `bash scripts/build` to rebuild the PWA dist, then restart the service.
- **Agents not running / runner not ready**: install and authenticate the selected CLI, confirm it is on the service's `PATH`, restart Proxima, then rescan in Settings -> Agents. For Grok, run `grok login` and verify with `grok models`.
- **PWA cannot install on phone**: the browser requires HTTPS. Use Tailscale Serve (step 6 above).
- **Want a fresh install**: stop the service, delete `~/.local/share/proxima/proxima.db`, and restart.
