# Linux Daily-Driver Acceptance

This is the support contract for a Proxima server on a Linux Mint NUC and a
CachyOS browser client over local access or Tailscale. Linux is **supported**.
macOS and Windows are **experimental** until each passes an equivalent complete
matrix.

The machine-readable owner is
`apps/api/proxima_api/platform_support.py`. The policy decision is
[ADR-0028](adr/0028-linux-first-daily-driver-support.md). Settings ->
Diagnostics shows the same catalog returned by `/api/config`.

## Run the matrix

From the repository root on Linux:

```bash
bash scripts/linux-daily-driver-acceptance
```

The command exits zero only when every row below passes. It requires the existing
`apps/api/.venv` development environment. It sets Master enabled for the
acceptance process only.

## Executable matrix

| Row | End-user contract | Executable evidence |
| --- | --- | --- |
| Platform | The API and Diagnostics call Linux supported and macOS/Windows experimental. Unknown hosts never fall through to Linux. | `test_platform_support.py`, `SettingsScreen.platform.test.tsx` |
| Install | `scripts/install-user` dry-runs without writes, installs into isolated XDG/HOME paths, preserves an existing Master-on config, and refuses a non-Linux host before dependency or service calls. | `test_install_user.py` |
| Service lifecycle | Status, restart, and stop select only the configured isolated systemd user unit. An unknown host stops before a service-manager call and explains the Linux path. | `test_linux_daily_driver_acceptance.py` |
| PTY / Terminal | A real Linux PTY starts in the selected project, completes a shell round trip, and reaps its shell and descendants on close. | `test_linux_daily_driver_acceptance.py`, `test_terminal.py` |
| Backup and restore | The online SQLite backup captures committed data, passes `PRAGMA integrity_check`, and restores into a separate target with the pre-backup value. | `test_linux_daily_driver_acceptance.py` |
| Diagnostics | Linux targets the configured systemd journal and reports active/stale work. Experimental macOS guidance does not attempt `journalctl`. | `test_debug_logs_route.py` |
| Preview | Default binding chooses a Tailscale address or loopback, never wildcard. The capability-gated relay serves root assets and WebSocket HMR without forwarding owner credentials, and stops with the app. | `test_preview_bind.py`, `test_preview_relay.py` |
| Local and Tailscale access | Local health and a synthetic HTTPS MagicDNS reverse-proxy entry reach the same isolated app; HTTPS entry mints a Secure owner cookie. | `test_linux_daily_driver_acceptance.py` |
| Upgrade readiness | Release identity is visible, the legacy apply path remains inert, Master stays enabled in its fixture, and no checkout, service, database, or config is changed. | `test_updates.py`, `test_linux_daily_driver_acceptance.py` |

The web support-label test and production build run separately:

```bash
npm --prefix apps/web test -- --run src/screens/SettingsScreen.platform.test.tsx
npm --prefix apps/web run build
```

The [real-browser evidence](evidence/linux-daily-driver/README.md) uses an isolated
raw Chrome DevTools Protocol pass. It asserts the platform labels, target copy,
Master visibility, and zero console errors before
writing and validating the PNG bytes.

## Isolation and prohibited operations

The matrix uses pytest temporary directories, fake `systemctl`/`uname` commands,
temporary SQLite databases, loopback-only preview processes, and a synthetic
reverse-proxy request. It does not:

- read or write the installed Proxima database
- restart, stop, or update the installed Proxima service
- call `tailscale set` or `tailscale serve`
- perform privileged enrollment
- modify signing keys, release manifests, or release custody

Real operator verification is limited to read-only observation: open the local URL,
open the existing Tailscale HTTPS MagicDNS URL from CachyOS, confirm Diagnostics
shows `Linux` and `Supported`, open a project Terminal, and load a project Preview.
Do not change host enrollment merely to run this matrix.

## Qualification limits

- The supported server claim is Linux-first, with systemd and the POSIX PTY backend.
- CachyOS is a browser client in this contract, not a second Proxima server install.
- macOS LaunchAgent and Windows Scheduled Task installers are experimental.
- Self-updating remains unavailable. Upgrade readiness means version visibility,
  data separation, and backup/restore evidence, not live self-promotion; updating
  is a manual git pull plus a service restart.
