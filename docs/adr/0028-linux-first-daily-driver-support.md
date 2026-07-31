# ADR-0028: Linux-first daily-driver support

- Status: Accepted
- Date: 2026-07-31

## Context

Proxima has installer and filesystem code for several host families, but packaging
presence is not the same as dependable daily-driver support. The complete owner
experience spans install, service lifecycle, PTY behavior, backup and restore,
diagnostics, app preview, local and Tailscale access, and upgrade readiness. A
partial claim makes unsupported actions risky and gives owners no single release
gate.

The first qualified deployment target is a Linux Mint NUC server used locally and
from a CachyOS browser client over Tailscale. macOS LaunchAgent and Windows
Scheduled Task packaging remain useful experiments, but neither has passed the same
end-to-end contract.

## Decision drivers

1. A support label must represent the complete daily owner journey.
2. Unsupported host actions must stop before build, config, service, or data writes.
3. Acceptance must be repeatable without touching the installed database or service.
4. The UI, API, operating guides, and acceptance evidence must share one owner.
5. Safe Self-Update enrollment, privileged setup, and release custody remain outside
   this decision.

## Options considered

1. Call every packaged platform supported. This is simple, but overstates PTY,
   diagnostics, backup scheduling, and service-manager evidence.
2. Remove non-Linux packaging. This avoids ambiguity but discards useful
   experimental paths.
3. Support Linux as the daily-driver contract and label macOS and Windows
   experimental until each passes the same matrix.

## Decision

Linux is the supported daily-driver server platform. The qualified target includes
a Linux browser client, specifically the CachyOS-over-Tailscale path in the
acceptance matrix. macOS and Windows are experimental.

`apps/api/proxima_api/platform_support.py` is the canonical machine-readable
catalog. `/api/config`, `/api/health`, and Settings Diagnostics project that
catalog. Installer and service wrappers identify the host before side effects and
fail closed with a supported or experimental alternative when the requested action
does not match.

`scripts/linux-daily-driver-acceptance` is the executable release gate. It composes
isolated fixture tests for install, lifecycle, PTY, backup/restore, diagnostics,
preview, access, and upgrade readiness. Its fixtures enable Master and keep Safe
Self-Update disabled. The gate uses fake service managers, temporary databases and
homes, loopback processes, and a synthetic Tailscale HTTPS reverse-proxy request.
It never enrolls a host, changes Tailscale configuration, or controls an installed
Proxima service.

## Consequences

Positive:

- Owners see an honest support tier in the product and install docs.
- Linux regressions have one executable, end-user-aligned release gate.
- Wrong-platform actions stop before mutations with actionable guidance.
- Experimental packages can improve without silently expanding the support claim.

Negative:

- macOS and Windows remain explicitly unqualified even when individual features work.
- The matrix must evolve whenever a daily-driver flow changes.
- Real Tailscale enrollment and production service operations remain operator
  evidence, not automation performed by the repository test suite.

## Related

- Acceptance matrix: [`../linux-daily-driver-acceptance.md`](../linux-daily-driver-acceptance.md)
- Installation: [`../installation.md`](../installation.md)
- Backup and restore: [`../backup.md`](../backup.md)
- Safe update authority: [`0008-external-safe-update-authority.md`](0008-external-safe-update-authority.md)
