# Runner conformance

Runner support is a capability contract, not a vendor-name allowlist. Central
`RunnerSpec` records describe what an adapter can prove, and Master route selection
reports the exact reason when a runner is rejected. Worker code must consume this
contract instead of branching on a runner name.

## Chat-only Master contract

A runner is selectable for Master only when its adapter sets
`master_chat_only=True`. This declaration means the adapter and its process boundary
prove all of the following:

- the model can exchange chat text and Proxima tool envelopes, but cannot invoke
  runner-native tools
- shell, browser, file read/write, skills, arbitrary MCP, and native permission
  escalation are unavailable
- the process receives a dedicated managed home and an empty read-only scratch,
  with no Container, Area, repo, source, runtime, config, or ordinary profile home
  mounted or exposed
- the exact capability selection `{"skills":[],"mcp":[]}` is applied on every run;
  omitted or null selections never inherit detected capabilities
- every native permission request is rejected

The route checks conformance before it creates a message or run, and the worker
checks again before process spawn. A runner switch uses the same check. Failure is
reported as `master_runner_not_conforming`; it never degrades to prompt-only trust.

## Current adapters

| Adapter | General chat | Master | Fail-closed reason |
| --- | --- | --- | --- |
| Claude Code ACP | supported | unsupported | Adapter cannot remove or confine native shell, file, browser, skill, and MCP capabilities |
| Codex app-server 0.145.0+ | supported | supported | Empty environments remove execution tools; a loopback provider firewall replaces every model-visible tool with exact server-owned broker schemas |
| Grok CLI ACP | supported | unsupported | Adapter does not prove the chat-only boundary |
| Hermes ACP | supported | unsupported | Adapter does not prove the chat-only boundary |
| Pi ACP | supported | unsupported | Adapter does not prove the chat-only boundary |

Codex is the only selectable production Master runner in this release. Version
0.145.0 is the minimum because the adapter depends on empty sticky environments,
empty selected capability roots, and dynamic tools. Installed older or
unparseable versions fail before a turn starts. `feature_master_orchestrator`
remains off by default.

Runner discovery publishes the adapter's static `masterChatOnly` declaration and
the host-specific `masterEligible` result plus `masterUnavailableReason`.
Eligibility calls the same `master_runner_conformance` boundary against the
server's controlled runtime path, so an absent, old, or unverifiable Codex is not
enabled by the Master selector. A legacy or unavailable current selection remains
a disabled explanatory state. Settings, message creation, and worker spawn still
repeat conformance rather than trusting the browser result. While external
maintenance is pending or active, or fence removal still holds exclusive ingress,
discovery skips process-backed conformance and reports Master ineligible with a
maintenance reason; both runner detection and the dashboard remain read-only
without launching a runner probe. Authoritative checks resume only after fence
state is clear and ingress admission resumes.

The Codex adapter starts app-server with strict configuration that disables shell,
browser, web search, apps, plugins, hooks, goals, image generation, subagents,
skills, MCP orchestration, permission requests, and inherited project
instructions. Each thread also sets `environments=[]`,
`runtimeWorkspaceRoots=[]`, `selectedCapabilityRoots=[]`, read-only sandboxing,
and `approvalPolicy=never`.
The restricted child ignores the ordinary runner environment inheritance and
allowlist escape hatches. It receives only the process variables needed to launch
Codex, the applicable OpenAI authentication, and HOME, XDG, TEMP, TMP, and TMPDIR
roots inside its dedicated managed home.

Every Master turn deletes its stored ACP session mapping and recycles the
restricted runner process before creating the next thread. Proxima then reapplies
the empty capability selection, reattests the exact dynamic broker schemas, and
rebuilds the bounded transcript from the run's immutable Focus epoch. No
provider-side conversation cache or prior Container process state is reused,
including consecutive turns inside the same Focus.

Codex may still construct runner-native utility schemas internally. A private
loopback provider firewall therefore discards the complete Codex tool carrier and
reconstructs it from `MasterToolBroker` definitions. It validates the exact names,
descriptions, and JSON schemas before forwarding. Missing, extra, duplicate, or
changed product schemas fail closed. Codex's HTTP fallback omits the dynamic carrier
after its WebSocket probe is rejected; that omission is accepted only after the
same process has registered the exact broker contract on `thread/start`. A missing
carrier without that pre-turn attestation fails closed. The provider bearer remains
an HTTP header inside trusted Proxima code and is never logged or inserted into
model input.
Runner-generated developer context is discarded and replaced by a fixed server-owned
filesystem-isolated policy. It forbids model-supplied, absolute host, internal graph,
and unrelated paths. Only validated `query_context` citations marked
`path_kind=scope_relative` may be repeated as provenance. The app-server retains its
dynamic-tool dispatch registration, so returned function calls route to the
in-process broker.

The loopback listener binds IPv4 loopback only and exposes one secret Responses
route. It rejects alternate routes, framing ambiguity, request compression,
redirects, compressed responses, and non-identity encodings. Provider responses
are bounded and fully buffered before any bytes are released to Codex, so an
oversized or malformed response cannot become a trusted partial stream. Connection
timeouts, cancellation, process exit, and restart close all listener tasks.

Version parsing is necessary but not sufficient. Before `turn/start`, app-server
must complete its strict initialize handshake, accept the exact dynamic broker
schemas on an ephemeral thread, return a thread identity, and install those same
schemas in the private firewall. Any behavioral mismatch fails before model input
is sent.

## Adding support

An adapter must first pass the real Master message-path conformance harness. The
harness probes shell, absolute paths, traversal, symlink escape, protected file
reads and writes, browser access, skills, arbitrary MCP, native tool execution, and
permission escalation while comparing protected canaries byte-for-byte. It also
proves restart and runner-switch capability reset. Only after those tests pass may
the adapter set `master_chat_only=True` and replace its fail-closed reason.
