# Releasing Proxima

Releases are what update user-managed installs: with update checks enabled,
Proxima checks GitHub Releases every 6 hours and shows the owner an "Update
available" notification with the release notes. Pushing to `main` alone notifies
nobody — only cutting a release does.

## The one command

From the **production checkout** (`main`, clean, after promoting work verified by
the staging service on `127.0.0.1:8767`):

```bash
scripts/release patch      # 0.2.0 → 0.2.1  (fixes)
scripts/release minor      # 0.2.0 → 0.3.0  (features)
scripts/release major      # 0.2.0 → 1.0.0  (breaking)
scripts/release 0.4.2      # explicit version
```

Before changing a version, the command verifies `origin` and GitHub both resolve to
`labsiqbal/proxima`, fast-forwards `main`, then runs `npm run qa`. The gates run in
this order:

1. backend suite and frontend production build
2. `npm run smoke:fresh`, which boots the current checkout with temporary
   `HOME`/XDG config/data/workspace roots and proves first-boot auth, database,
   project provisioning, Proxima identity, exact disabled feature flags, and no
   retired-product config/path reads
3. `npm run smoke`, the live API plus desktop/mobile browser smoke against the
   already-running staging service on port `8767` by default

The fresh-install smoke is self-contained and always stops its temporary server.
It can be run independently; `PROXIMA_SMOKE_PYTHON` selects a Python interpreter,
an existing `PYTHONPATH` is preserved, and `PROXIMA_SMOKE_PORT` can reserve a
known-free port in CI. Sandboxes that explicitly prohibit all AF_INET sockets may
run `PROXIMA_SMOKE_IN_PROCESS=1 npm run smoke:fresh`; that opt-in uses FastAPI's
`TestClient` under the same isolated environment and is not a substitute for the
default TCP mode in release QA. Override the live target only when testing an
equivalent staging deployment:

```bash
PROXIMA_SMOKE_BASE=https://proxima-staging.minarflow.com scripts/release patch
```

After QA passes, the command updates `VERSION`, Python project metadata and
`uv.lock`, root/web npm metadata, and the web `package-lock.json`. It verifies the
synced values, commits `release: vX.Y.Z`, creates an annotated tag, atomically
pushes `main` plus the tag, and creates the GitHub Release in
`labsiqbal/proxima` with generated notes.

## Rules

- **`main` must always be releasable.** `proxima update` on user machines
  pulls `main` HEAD (not the tag), so never park unfinished work there —
  that's what the `staging` branch is for.
- Requires an authenticated `gh` CLI; the script aborts cleanly otherwise.
- Never bypass the release script's QA or repository-identity checks.
- Release notes are user-facing — write for users.
- If the script fails AFTER pushing (network blip during the final step), the
  tag `vX.Y.Z` exists on GitHub but the Release object doesn't — installs see
  nothing (they watch Releases, not tags). Don't re-run the script (it would
  cut a new version); finish the failed step by hand:
  `gh release create vX.Y.Z --repo labsiqbal/proxima --title vX.Y.Z --generate-notes`.

## Post-release verification

Verify `vX.Y.Z` on the public `labsiqbal/proxima` repository, then check
`https://proxima.minarflow.com/api/health`. Keep
`https://proxima-staging.minarflow.com` on the staging branch and service. Both
domains must remain protected by Cloudflare Access.

## What users see

Within 6 hours (or on "Check for updates" in Settings), user installs with update
checks enabled show a sidebar pill → release-notes modal → one-click **Update
now** (Linux/macOS; Windows gets manual instructions). Root-owned system services
use the administrator-driven update documented in `infra/systemd/README.md`. See
`docs/installation.md#updating`.
