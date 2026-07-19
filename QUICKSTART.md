# Proxima Quickstart

## 1. Prerequisite checks

Confirm all three tools are available before you start:

```bash
uv --version
npm --version
hermes --version
```

You also need an authenticated Hermes install. Check for credentials:

```bash
ls ~/.hermes/auth.json ~/.hermes/config.yaml 2>/dev/null && echo "Hermes credentials found"
```

If Hermes is missing or not authenticated, install and authenticate it first. Proxima will install without it but agents won't run until Hermes is set up.

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
- Keeps Design Studio disabled; image generation and Workflow Graph remain available.

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
| Hermes profiles | `~/.local/share/proxima/hermes-profiles/` |
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
| `PROXIMA_FEATURE_DESIGN_STUDIO` | `0` | Temporarily disables Design Studio |

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
- **Agents not running / "Hermes not found"**: install the Hermes CLI, authenticate with `hermes -z`, confirm it is on `PATH`, then `systemctl --user restart proxima`.
- **PWA cannot install on phone**: the browser requires HTTPS. Use Tailscale Serve (step 6 above).
- **Want a fresh install**: stop the service, delete `~/.local/share/proxima/proxima.db`, and restart.
