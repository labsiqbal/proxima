# Backup & recovery

Proxima stores everything in a single SQLite database (plus project files on
disk). `scripts/backup` makes a **consistent online snapshot** using SQLite's
backup API (safe to run while the server is up, WAL and all), compacts it, and
rotates old copies.

## Manual backup

```bash
PROXIMA_DB_PATH=~/.local/share/proxima/proxima.db bash scripts/backup
```

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `PROXIMA_DB_PATH` | `~/.local/share/proxima/proxima.db` | live database |
| `PROXIMA_BACKUP_DIR` | `<db dir>/backups` | where snapshots are written |
| `PROXIMA_BACKUP_KEEP` | `14` | how many snapshots to retain |

Each run writes `proxima-YYYYMMDD-HHMMSS.db` and prunes anything beyond the
newest `PROXIMA_BACKUP_KEEP`.

> Also back up the project files directory (under your workspace root) if your
> agents write artifacts you care about — those live on disk, not in the DB.

## Scheduled backup (cron)

Daily at 03:00:

```cron
0 3 * * *  PROXIMA_DB_PATH=$HOME/.local/share/proxima/proxima.db /path/to/proxima/scripts/backup >> $HOME/.local/share/proxima/backup.log 2>&1
```

## Scheduled backup (systemd timer)

The Linux `scripts/install-user` installer creates and enables these units
automatically. Equivalent system-wide templates live under `infra/systemd/`.

`~/.config/systemd/user/proxima-backup.service`:

```ini
[Unit]
Description=Proxima database backup

[Service]
Type=oneshot
Environment=PROXIMA_DB_PATH=%h/.local/share/proxima/proxima.db
ExecStart=/path/to/proxima/scripts/backup
```

`~/.config/systemd/user/proxima-backup.timer`:

```ini
[Unit]
Description=Daily Proxima backup

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now proxima-backup.timer
```

macOS and Windows installers do not create a backup schedule. Run
`scripts/backup` from a shell with the Proxima variables loaded, or schedule that
same command with launchd/Task Scheduler. Staging must use its own
`PROXIMA_DB_PATH` and backup directory under the `proxima-staging` data root.

## Restore

Stop the server, then copy a snapshot over the live database:

```bash
cp ~/.local/share/proxima/backups/proxima-YYYYMMDD-HHMMSS.db \
   ~/.local/share/proxima/proxima.db
```

Verify before starting back up:

```bash
sqlite3 ~/.local/share/proxima/proxima.db "PRAGMA integrity_check;"
```
