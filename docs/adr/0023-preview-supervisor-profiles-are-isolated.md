# ADR-0023: Preview supervisor profiles are isolated

- Status: Accepted
- Date: 2026-07-31

## Context

Production and staging can run different commits. Sharing one supervisor socket
or executable lets one profile launch the other profile's code and protocol,
breaking staging isolation and making upgrades fail closed unpredictably.

## Decision drivers

- Keep production and staging checkout code independent.
- Detect protocol skew before API restart.
- Prevent cross-profile state or control-channel adoption.
- Make upgrades explicit and reversible at the supervisor boundary.

## Options considered

1. Share one socket and production executable. Staging depends on production
   code and rollout order.
2. Share a socket with a profile field in requests. The accepting executable is
   still selected before authentication.
3. Give every profile its own socket, instance unit, executable, protocol
   identity, and state root.

## Decision

Production and staging use separate socket and instance-service units. Each unit
executes its own checkout and declares a distinct profile-bound protocol identity
and state root. Client handshake and restart adoption require an exact profile
and protocol match. Deployment updates install, reload, enable, and probe the
matching units before restarting that API profile.

## Consequences

- Staging can carry a new supervisor protocol before production.
- Cross-profile connections and durable records fail closed.
- Existing installations require an explicit unit migration during upgrade.

## Related

- [ADR-0021](0021-preview-supervisors-own-app-scopes.md)
- `infra/systemd/README.md`
- `docs/installation.md`
