# Safe-update trusted probes

This source copy is a bootstrap input only. An enrolled updater uses a separately
installed root-owned copy and a policy-pinned tree digest. Candidate releases do
not choose or write the installed bundle, its browser selectors, or its evidence.
The installed `probe.py` starts the frozen candidate in its isolated loopback
namespace and runs every scenario in `browser-scenarios.json` after the fixed API,
authenticated maintenance, SSE, version, and served-asset checks pass.
