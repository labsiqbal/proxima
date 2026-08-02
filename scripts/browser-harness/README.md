# Browser verification harness

Shared Chrome DevTools driver for the repo's browser E2E verification scripts
(`scripts/verify_*_browser.py`). It launches a headless Chromium/Chrome with a
throwaway profile, speaks the DevTools protocol over a raw WebSocket, and runs
declarative scenarios (click / assert / screenshot steps) against a freshly
started Proxima instance.

- `browser.py` - the driver: `run_scenario(...)` plus the lower-level
  `_WebSocket` / `_evaluation` helpers the verify scripts use directly.
- `browser-scenarios.json` - declarative scenarios; currently the
  `master-popup-home` scenario used by `scripts/verify_master_browser.py`.
  Most verify scripts build their scenario dicts inline instead.
- `codex-fixture` - a version-only fake `codex` executable the verify scripts
  put on `PATH` so runner detection sees a Master-eligible Codex without ever
  invoking a real model.

Unit coverage for the driver's network-evidence parsing lives in
`apps/api/tests/test_file_target_browser_fixture.py`.

This harness came out of the deleted safe-updater trusted-probe bundle
(`trusted-probes/safe-update/`, prune A1); only the generic driver survived
because the verify scripts depend on it.
