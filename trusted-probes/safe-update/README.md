# Safe-update trusted probes

This source copy is a bootstrap input only. An enrolled updater uses a separately
installed root-owned copy and a policy-pinned tree digest. Candidate releases do
not choose or write the installed bundle, its browser selectors, or its evidence.
The installed `probe.py` starts the frozen candidate in its isolated loopback
namespace and runs every scenario in `browser-scenarios.json` after the fixed API,
authenticated maintenance, SSE, version, and served-asset checks pass.

`codex-fixture` is the bundle-owned version-only runner used by Master selector
scenarios. The controller mounts it read-only through the sandbox's explicit
auxiliary-tool boundary. Before starting the candidate, the trusted probe requires
the exact `/opt/proxima-tools/codex` path and version, a refused turn invocation,
and a denied write. The local replay copies this same tracked fixture into its
temporary executable directory.
