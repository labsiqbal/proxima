# Proxima systemd profiles

These templates are for a system-wide Linux deployment. The recommended layout is
root-owned application code plus a non-login `proxima` service user that owns only
runtime data. `scripts/install-local` is a copy/build helper for manual foreground
use; it does **not** create the user, install units, or enable services. Use the
complete sequence below for a managed deployment.

| Profile | Unit | Checkout | Config | Data | Local port | Hostname |
| --- | --- | --- | --- | --- | --- | --- |
| Production | `proxima.service` | `/opt/proxima` | `/etc/proxima/proxima.env` | `/var/lib/proxima` | `8765` | `proxima.minarflow.com` |
| Staging | `proxima-staging.service` | `/opt/proxima-staging` | `/etc/proxima-staging/proxima.env` | `/var/lib/proxima-staging` | `8767` | `proxima-staging.minarflow.com` |

Both public hostnames must remain behind Cloudflare Access (or an equivalent
owner-only gate). The services bind to loopback; the tunnel is the only public
route. Production uses `proxima-backup.service` and `proxima-backup.timer`.
Production uses `proxima-preview-output.socket`; staging uses
`proxima-staging-preview-output.socket`. Each accepted connection starts a
profile-specific supervisor from that profile's checkout and state root. The
supervisor launches one preview app in its own service cgroup, owns its bounded
output, and exposes a profile-bound reconnect capability. A restarted API adopts
only an exact saved broker, process, cgroup, profile, protocol, and lineage match.

## 1. Install prerequisites and code

Install `git`, `uv`, Node.js, and `npm` in system-visible locations. At least one
runner CLI must also be visible in the service's `PATH`; a runner installed only in
an operator's shell or version manager is not visible to systemd.

Create the service identity and clone the production branch:

```bash
id -u proxima >/dev/null 2>&1 || sudo useradd --system \
  --home-dir /var/lib/proxima --create-home --shell /usr/sbin/nologin proxima

sudo git clone --branch main --single-branch \
  https://github.com/labsiqbal/proxima.git /opt/proxima
sudo bash -c 'cd /opt/proxima/apps/api && uv sync --frozen'
sudo npm --prefix /opt/proxima/apps/web ci
sudo npm --prefix /opt/proxima/apps/web run build
sudo chown -R root:root /opt/proxima
sudo ln -sfn /opt/proxima/scripts/proxima /usr/local/bin/proxima
```

`uv`, `node`, and `npm` must resolve inside `sudo` for the build commands. If they
do not, install them system-wide or pass an explicit trusted `PATH` to `sudo`.

## 2. Create production data and config

```bash
sudo install -d -o proxima -g proxima -m 0750 \
  /var/lib/proxima \
  /var/lib/proxima/workspace \
  /var/lib/proxima/hermes-profiles \
  /var/lib/proxima/backups
sudo install -d -o root -g proxima -m 0750 /etc/proxima

sudo tee /etc/proxima/proxima.env >/dev/null <<'EOF'
PROXIMA_REPO_ROOT=/opt/proxima
PROXIMA_CONFIG=/etc/proxima/proxima.env
PROXIMA_DATA_DIR=/var/lib/proxima
PROXIMA_DB_PATH=/var/lib/proxima/proxima.db
PROXIMA_WORKSPACE_ROOT=/var/lib/proxima/workspace
PROXIMA_HERMES_PROFILES_ROOT=/var/lib/proxima/hermes-profiles
PROXIMA_SOURCE_HERMES_HOME=/var/lib/proxima/.hermes
PROXIMA_WEB_DIST=/opt/proxima/apps/web/dist
PROXIMA_HOST=127.0.0.1
PROXIMA_PORT=8765
PROXIMA_SERVICE_NAME=proxima
PROXIMA_UPDATE_CHECK=0
PROXIMA_UPDATE_REPO=labsiqbal/proxima
PROXIMA_FEATURE_DESIGN_STUDIO=0
EOF
sudo chown root:proxima /etc/proxima/proxima.env
sudo chmod 0640 /etc/proxima/proxima.env
```

The config contains no provider credential. Put runner authentication in the
runner's own home while executing as the service user. For Hermes, for example:

```bash
sudo -u proxima -H env HOME=/var/lib/proxima \
  PATH=/usr/local/bin:/usr/bin:/bin hermes -z
```

For Grok on a headless service host, use device authentication:

```bash
sudo -u proxima -H env HOME=/var/lib/proxima \
  PATH=/usr/local/bin:/usr/bin:/bin grok login --device-auth
```

Use the equivalent login command for Claude Code, Codex, or Pi. Confirm both
the runner executable and its authenticated files are readable by `proxima`; do
not copy an operator's master credentials into `/etc/proxima`.

## 3. Install and enable production units

```bash
sudo install -o root -g root -m 0644 \
  infra/systemd/proxima.service.example \
  /etc/systemd/system/proxima.service
sudo install -o root -g root -m 0644 \
  infra/systemd/proxima-preview-output.socket \
  /etc/systemd/system/proxima-preview-output.socket
sudo install -o root -g root -m 0644 \
  infra/systemd/proxima-preview-output@.service.example \
  /etc/systemd/system/proxima-preview-output@.service
sudo install -o root -g root -m 0644 \
  infra/systemd/proxima-backup.service.example \
  /etc/systemd/system/proxima-backup.service
sudo install -o root -g root -m 0644 \
  infra/systemd/proxima-backup.timer.example \
  /etc/systemd/system/proxima-backup.timer

sudo systemctl daemon-reload
sudo systemctl enable --now proxima-preview-output.socket
sudo -u proxima env \
  PROXIMA_REPO_ROOT=/opt/proxima \
  PROXIMA_CONFIG=/etc/proxima/proxima.env \
  PROXIMA_DATA_DIR=/var/lib/proxima \
  PROXIMA_OUTPUT_BROKER_SOCKET=/run/proxima-preview-output.sock \
  PROXIMA_OUTPUT_BROKER_PROTOCOL=proxima-preview-supervisor-v2:production \
  PROXIMA_PREVIEW_PROFILE=production \
  PROXIMA_PREVIEW_SCOPE_STATE_ROOT=/var/lib/proxima/preview-supervisors \
  /opt/proxima/scripts/proxima preview-broker-check
sudo systemctl enable --now proxima.service proxima-backup.timer
sudo systemctl status proxima.service --no-pager
curl --fail --silent http://127.0.0.1:8765/api/health
```

The application and backup units write runtime data only under `/var/lib/proxima`.
Code and config remain root-owned.

## 4. Add isolated staging

Clone the staging branch into a separate checkout, then create its data and config
without sharing production state:

```bash
sudo git clone --branch staging --single-branch \
  https://github.com/labsiqbal/proxima.git /opt/proxima-staging
sudo bash -c 'cd /opt/proxima-staging/apps/api && uv sync --frozen'
sudo npm --prefix /opt/proxima-staging/apps/web ci
sudo npm --prefix /opt/proxima-staging/apps/web run build
sudo chown -R root:root /opt/proxima-staging

sudo install -d -o proxima -g proxima -m 0750 \
  /var/lib/proxima-staging \
  /var/lib/proxima-staging/workspace \
  /var/lib/proxima-staging/hermes-profiles
sudo install -d -o root -g proxima -m 0750 /etc/proxima-staging
sudo tee /etc/proxima-staging/proxima.env >/dev/null <<'EOF'
PROXIMA_REPO_ROOT=/opt/proxima-staging
PROXIMA_CONFIG=/etc/proxima-staging/proxima.env
PROXIMA_DATA_DIR=/var/lib/proxima-staging
PROXIMA_DB_PATH=/var/lib/proxima-staging/proxima.db
PROXIMA_WORKSPACE_ROOT=/var/lib/proxima-staging/workspace
PROXIMA_HERMES_PROFILES_ROOT=/var/lib/proxima-staging/hermes-profiles
PROXIMA_SOURCE_HERMES_HOME=/var/lib/proxima-staging/.hermes
PROXIMA_WEB_DIST=/opt/proxima-staging/apps/web/dist
PROXIMA_HOST=127.0.0.1
PROXIMA_PORT=8767
PROXIMA_SERVICE_NAME=proxima-staging
PROXIMA_UPDATE_CHECK=0
PROXIMA_UPDATE_REPO=labsiqbal/proxima
PROXIMA_FEATURE_DESIGN_STUDIO=0
EOF
sudo chown root:proxima /etc/proxima-staging/proxima.env
sudo chmod 0640 /etc/proxima-staging/proxima.env

sudo -u proxima env HOME=/var/lib/proxima-staging \
  PATH=/usr/local/bin:/usr/bin:/bin hermes -z

sudo install -o root -g root -m 0644 \
  infra/systemd/proxima-staging.service.example \
  /etc/systemd/system/proxima-staging.service
sudo install -o root -g root -m 0644 \
  infra/systemd/proxima-staging-preview-output.socket \
  /etc/systemd/system/proxima-staging-preview-output.socket
sudo install -o root -g root -m 0644 \
  infra/systemd/proxima-staging-preview-output@.service.example \
  /etc/systemd/system/proxima-staging-preview-output@.service
sudo systemctl daemon-reload
sudo systemctl enable --now proxima-staging-preview-output.socket
sudo -u proxima env \
  PROXIMA_REPO_ROOT=/opt/proxima-staging \
  PROXIMA_CONFIG=/etc/proxima-staging/proxima.env \
  PROXIMA_DATA_DIR=/var/lib/proxima-staging \
  PROXIMA_OUTPUT_BROKER_SOCKET=/run/proxima-staging-preview-output.sock \
  PROXIMA_OUTPUT_BROKER_PROTOCOL=proxima-preview-supervisor-v2:staging \
  PROXIMA_PREVIEW_PROFILE=staging \
  PROXIMA_PREVIEW_SCOPE_STATE_ROOT=/var/lib/proxima-staging/preview-supervisors \
  /opt/proxima-staging/scripts/proxima preview-broker-check
sudo systemctl enable --now proxima-staging.service
curl --fail --silent http://127.0.0.1:8767/api/health
```

Production and staging use the same Unix service identity, so their separation is
by explicit checkout/config/data paths, not by OS-user isolation. Do not point one
profile at the other's paths.

## Updates and ownership

The root-owned layout intentionally prevents the web process from changing its own
checkout, virtual environment, or web build, and a non-login service user cannot
restart a system unit through `sudo`. Therefore **Update now** and
`sudo -u proxima proxima update` are not supported for this layout. Keep
`PROXIMA_UPDATE_CHECK=0` and update as an administrator:

```bash
sudo git -C /opt/proxima pull --ff-only
proxima_was_active=0
if sudo systemctl is-active --quiet proxima.service; then
  proxima_was_active=1
  sudo systemctl stop proxima.service
fi
if ! sudo -u proxima /opt/proxima/scripts/check-preview-drained \
  --protocol proxima-preview-supervisor-v2:production; then
  if [ "$proxima_was_active" = 1 ]; then
    sudo systemctl start proxima.service
  fi
  echo "Proxima update refused; the prior service was restored." >&2
  exit 1
fi
sudo bash -c 'cd /opt/proxima/apps/api && uv sync --frozen'
sudo npm --prefix /opt/proxima/apps/web ci
sudo npm --prefix /opt/proxima/apps/web run build
sudo chown -R root:root /opt/proxima
sudo install -o root -g root -m 0644 \
  /opt/proxima/infra/systemd/proxima.service.example \
  /etc/systemd/system/proxima.service
sudo install -o root -g root -m 0644 \
  /opt/proxima/infra/systemd/proxima-preview-output.socket \
  /etc/systemd/system/proxima-preview-output.socket
sudo install -o root -g root -m 0644 \
  /opt/proxima/infra/systemd/proxima-preview-output@.service.example \
  /etc/systemd/system/proxima-preview-output@.service
sudo systemctl daemon-reload
sudo systemctl enable --now proxima-preview-output.socket
sudo -u proxima env \
  PROXIMA_REPO_ROOT=/opt/proxima \
  PROXIMA_CONFIG=/etc/proxima/proxima.env \
  PROXIMA_DATA_DIR=/var/lib/proxima \
  PROXIMA_OUTPUT_BROKER_SOCKET=/run/proxima-preview-output.sock \
  PROXIMA_OUTPUT_BROKER_PROTOCOL=proxima-preview-supervisor-v2:production \
  PROXIMA_PREVIEW_PROFILE=production \
  PROXIMA_PREVIEW_SCOPE_STATE_ROOT=/var/lib/proxima/preview-supervisors \
  /opt/proxima/scripts/proxima preview-broker-check
sudo systemctl restart proxima.service
curl --fail --silent http://127.0.0.1:8765/api/health
```

For staging, perform the same sequence from `/opt/proxima-staging`, install the
two `proxima-staging-preview-output*` units plus
`proxima-staging.service`, verify the
`proxima-preview-supervisor-v2:staging` protocol identity, and only then restart
`proxima-staging.service`. Never point one profile at the other profile's socket,
checkout, protocol, or state root. Enabling a socket does not stop active
per-preview supervisor instances; they remain available for exact adoption or
finish draining at EOF.

Before either profile migration, stop its API service gracefully and run
`scripts/check-preview-drained` as the service user with that profile's new protocol.
The service shutdown asks the prior manager to drain every preview. The check scans
same-user procfs authority, lineage, preview environment, and service-cgroup evidence,
then refuses before any unit is replaced when an older app or broker survives. Restart
the prior service on refusal. When production and staging share the service user, drain
and verify both profiles first.

The safe-updater foundation adds contract-only root-owned controller and candidate
unit templates. The candidate template carries defence-in-depth systemd sandbox
directives but still has `ExecStart=/bin/false`; it is not an enrollment artifact and
must not be enabled. Do not make the `proxima` user own the checkout or grant it
restart authority. A later administrator-only enrollment must qualify the
external journal/pointer/fence roots, dedicated candidate sandbox, trusted release
keys and probes, disk reserve, and stop/start verification before enabling any
activation. See the
[adapter qualification contract](../../docs/adding-safe-updater-adapter.md).
