# Safe updater foundation

This directory contains only the root-admin deployment contract for the external
safe updater. It is not installed by the normal user installer, and no script in
this repository enables a service, switches a release, or changes a live database.

The controller owns its journal, lock, pointers, fence, evidence, and backups
outside candidate releases. The application can submit a narrowly typed request
and render an authenticated projection, but cannot write those files.

`install-safe-updater` is intentionally fail-closed until the later candidate
sandbox, trusted probe, and service-manager qualification work lands.
