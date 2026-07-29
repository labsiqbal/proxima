# Safe-update trusted probes

This source copy is a bootstrap input only. An enrolled updater uses a separately
installed root-owned copy and a policy-pinned tree digest. Candidate releases do
not choose or write the installed bundle, its browser selectors, or its evidence.
