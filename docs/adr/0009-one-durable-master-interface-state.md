# ADR-0009: One durable Master interface state

- Status: Accepted
- Date: 2026-07-30

## Context

Master is available as a full-page home and as a popup from other authenticated
surfaces. Both presentations expose one conversation, one active turn, Focus,
message targeting, notifications, and connection state. Independent stores or
streams would allow the two presentations to disagree, duplicate a submission,
lose a draft, or place durable events in different histories.

Runner selection also crosses this interface boundary. An adapter declaration is
not enough to make a host runner selectable. Installation and minimum-version
checks must use the same server-owned conformance decision that protects message
creation and process spawn.

## Decision drivers

1. One durable conversation must remain canonical across every presentation.
2. Reconnect and replay must not duplicate messages, projections, or toasts.
3. Presentation changes must preserve drafts, Focus, targets, and scroll state.
4. Accessibility semantics must remain consistent on desktop and mobile.
5. Runner choices must fail closed before the owner can select them.

## Options considered

1. Give the Home and popup independent state and reconcile them after changes.
   Rejected because reconciliation permits transient split-brain behavior and
   duplicate live connections.
2. Keep shared server data but let each presentation own local draft, Focus, and
   stream state. Rejected because switching presentations can lose or fork the
   active interaction.
3. Mount one authenticated provider above the shell and render Home and popup as
   consumers of that provider. Chosen.

## Decision

`MasterStateProvider` is the only frontend owner of the Master desk, ordered
thread, active turn, durable event cursor, SSE connection, reconnect
reconciliation, draft, selection, Focus, target, popup state, toast queue, and
stable scroll and panel state.

`MasterScreen`, `MasterPopup`, `MasterConversation`, and `MasterComposer` consume
that interface. Opening or closing a presentation never creates another Master
session, composer, or event stream.

Runner discovery publishes both the static adapter declaration and the dynamic
host conformance result with a fail-closed reason. The selector enables only
entries whose dynamic result is eligible. A stored unavailable runner remains a
disabled explanatory option. Settings and runtime boundaries repeat conformance
before mutation or process spawn.

The popup is a modal dialog with a labeled trigger, focus containment, Escape
close, focus restoration, labeled controls, and a bridge to the full Home. The
Home exposes labeled Focus, history, runner, work-panel, and composer controls.

## Consequences

**Positive**

- Home and popup cannot diverge or create duplicate submissions and streams.
- Reconnect, replay, and presentation changes preserve one durable ordering.
- Host-ineligible runners are explained but cannot be newly selected.
- Accessibility behavior is exercised through one shared component boundary.

**Negative / accepted trade-offs**

- Provider lifecycle and owner transitions require strict cancellation of stale
  requests and streams.
- Presentation-only state must remain clearly separated from durable server
  state.

## Related

- [Master supervision and durable projections](../master-supervision.md)
- [Runner conformance](../runner-conformance.md)
- [Master architecture flow](../reference/architecture.md#1a-master-delegation-and-unattended-queue)
- [Integrated acceptance](../master-integrated-acceptance.md)
