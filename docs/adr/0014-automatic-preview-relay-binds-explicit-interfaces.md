# ADR-0014: Automatic preview relay binds explicit interfaces

- Status: Accepted
- Date: 2026-07-31

## Context

Local browser origins and Tailscale browser origins need access to the
capability-gated preview relay. Binding only the tailnet address breaks a local
UI, while a wildcard bind exposes more interfaces than intended.

## Decision drivers

- Keep local preview reachable from a loopback UI.
- Keep tailnet preview reachable from a Tailscale UI.
- Avoid wildcard relay exposure.
- Preserve one capability-gated relay contract.

## Options considered

1. Bind only the selected tailnet address. This breaks local origins.
2. Bind a wildcard address. This exposes the relay on unintended interfaces.
3. Bind the same relay port separately on loopback and the selected Tailscale
   interface.

## Decision

Automatic preview relay binding creates explicit listeners on loopback and, when
present, the selected Tailscale address. Both listeners enforce the same
capability contract. Automatic mode never broadens to a wildcard bind.

## Consequences

- Local and tailnet origins reach an authenticated relay at an address they can
  route to.
- Operators may see two explicit listeners for one configured relay port.
- Other network interfaces remain unbound.

## Related

- Extracts the relay-binding decision from ADR-0011.
- `docs/reference/architecture.md`
- `docs/CAPABILITIES.md`
