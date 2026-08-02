# ADR-0040: Files is a destination, not a right-rail tool

- Status: Accepted
- Date: 2026-08-01

## Context

The shell splits its surfaces in two. **Destinations** live in the left
navigation, own the main pane, and carry the flow: Chat, Tasks, Workflows,
Archive, Design. **Tools** live on a slim right rail and open as an overlay above
whatever you were reading: Terminal, Files, Preview. The split exists so that
opening a shell or peeking at a file never loses the plan or thread you were on.

Files fit that rule less well than the other two. Terminal and Preview are
*actions on the current project* - you open one, do a thing, close it. Browsing a
Container is navigation: it is how an owner answers "what is actually in this
project", which is the same question Archive answers for produced deliverables.
Owners looked for it in the left navigation and read its absence there as a
missing feature.

Two concrete limits made the tool framing worse:

- The overlay is scoped to one active project by construction. Delegate is a
  global mode - its Tasks and Archive already span every Container - but a
  right-rail panel has nowhere to put a project filter, so Delegate had no way to
  browse files at all.
- Clicking a file in the tool opened `FileEditor`, a code editor. Every other
  surface (Chat outputs, Task outputs, Archive records) opens files in the
  ArtifactViewer, so the same PDF, image, or Markdown file rendered differently
  depending on which surface you reached it from.

## Decision drivers

1. A surface owners navigate to belongs in the navigation.
2. Delegate's global scope must reach files the same way it reaches Tasks.
3. One file should render one way, whatever surface opened it.
4. Terminal and Preview keep the overlay behaviour that motivated the rail.
5. Existing entry points ("Reveal in Files", Ops-migration recovery) must keep
   working without each caller learning where Files now lives.

## Options considered

1. Keep Files on the rail and add a project switcher inside the panel. This
   duplicates the sidebar's switcher in a narrow overlay and still leaves
   browsing outside the navigation.
2. Add a second, Delegate-only Files destination beside the tool. Two surfaces
   for one concept, and the editor/viewer split remains.
3. Promote Files to a destination in both navigations and remove the tool.

## Decision

Files is a destination in both navigations. `FilesScreen` serves both scopes,
which differ only in how many trees are on the page:

- **Work** renders the active Container, matching the sidebar's project switcher.
- **Delegate** renders every Container, narrowed by a head filter, the same shape
  Tasks and Archive already take in that mode.

Opening a file always goes through the **ArtifactViewer**, so a file browsed here
renders exactly as it does from Chat, Tasks, or Archive.

`Tool` narrows to `'terminal' | 'preview'` and the rail no longer carries a Files
panel. The `proxima:reveal-file` window event keeps its contract - path,
`projectSlug`, `pathKind`, `rootSide` - but the app shell now listens for it and
switches to the Files destination, so callers such as Archive's "Reveal in Files"
and the Ops-migration recovery screen are unchanged. `rootSide: 'container'`
still selects the read-only container-inspection adapter, which recovery depends
on to read a Container root mid-migration.

## Consequences

Positive:

- Browsing files is where owners look for it, and Delegate can browse at all.
- One renderer for files across every surface.
- The right rail is back to two genuinely overlay-shaped tools.

Negative:

- Files no longer floats above the current screen, so peeking at a file while
  reading a plan now costs a navigation. The deep stack's Back returns, but it is
  a real change in feel for owners who used it that way.
- The Work navigation grows a sixth destination.
- Inline editing moved with the viewer: `FileEditor` is still reachable from the
  viewer's edit action rather than from a single click in the tree.

## Related

- Shell information architecture: [`../ui-shell.md`](../ui-shell.md)
- Capabilities: [`../CAPABILITIES.md`](../CAPABILITIES.md)
- Master ships enabled: [`0039-master-orchestrator-ships-enabled.md`](0039-master-orchestrator-ships-enabled.md)
- Owner-safe Container activity: [`0038-owner-safe-container-activity-boundaries.md`](0038-owner-safe-container-activity-boundaries.md)
