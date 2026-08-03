# ADR-0043: Artifacts is the destination; the gallery is the main window

- Status: Accepted
- Date: 2026-08-03

## Context

[ADR-0040](0040-files-is-a-destination.md) promoted the file tree from a
right-rail tool to a left-navigation destination, and prune Part D (#139, per
decision #122) merged the deliverable ledger into it as a set of lenses:
**Browse** (the real disk), **Deliverables** (the record ledger), **History**
(records whose file is gone).

Living with that shipped shell answered the question it was meant to answer.
Opening the destination put a **file tree** in the main window - the widest,
most valuable surface in the app - and the first thing an owner sees after a
day of agent work is a folder hierarchy. It is a correct picture of the disk and
a poor picture of the *work*: designs, images, rendered video, and documents all
read as identical rows of text. The product had a thumbnail gallery before the
merge and the merge lost it.

Two facts point the same way:

- Deliverables are things you **look at**. A design, a screenshot, an mp4, and a
  report are recognised by their picture, not by their filename.
- Browsing the tree is a **utility**, not a destination: it answers "where is
  that file", which is the shape of a tool you open next to your work, exactly
  the shape the rail was built for.

## Decision drivers

1. The main window should show the work, not the filesystem holding it.
2. Recognition beats reading: thumbnails for designs, images, and video.
3. The deliverable ledger (#139) - badges, lineage, approval, version chains -
   is a keeper and must survive the change intact, backend untouched.
4. Old `#archive/<project>/<slug>` permalinks must keep resolving.
5. Both navigations (Work and Delegate) show the same destination.
6. Leave clean seams for the follow-on tickets rather than pre-building them.

## Options considered

1. **Keep Files, add a gallery lens.** Four lenses on one screen, and the
   default main window is still a tree. It buries the answer under a chip.
2. **Two destinations - Files and Artifacts.** Two names for "where my work
   is", the exact overlap decision #122 removed a month earlier.
3. **Replace Files with Artifacts; browsing becomes a dock tool again.** One
   destination, gallery-first, and the utility goes back to the rail it fits.

## Decision

**Artifacts replaces Files as the destination in both navigations.** Its main
window is a gallery of what the project produced:

- **Designs, images, and video render as thumbnails** (`ArtifactThumb`: a
  design draws its first artboard from `scene.json` through the same
  `MiniPreview` the Design gallery and the record panel use; images come from
  the preview endpoint, except SVG/XML, which the backend serves inert per
  ADR-0042 and therefore needs authenticated raw bytes; video renders a
  metadata-only poster frame so a grid never downloads whole films).
- **Documents render as a list** - a row per doc/page/data file with its kind.
- Three **tabs on one surface**, not sub-screens: **All** (the gallery),
  **Deliverables** (the #139 ledger with lineage, approval, and version
  chains), **History** (records whose file is gone from disk).
- Artifacts the ledger knows carry the **deliverable badge** on their card or
  row; clicking it opens the record's permanent address, exactly as the tree
  row badge did.
- `#archive/<project>/<slug>` permalinks resolve into Artifacts, and a
  bookmarked `?view=files` URL lands on Artifacts rather than the default view.

The owner decision set locked on 2026-08-03, recorded here as the decision:

1. The Files destination is **replaced entirely**; full tree browsing moves to
   the right dock (#145).
2. **One gallery with tabs**, never separate sub-screens per lens.
3. Documents will open in the existing **wiki/markdown editor** (#146).
4. The **app preview viewport** will render in the main window (#147).

Scope of this record: the destination, the gallery, the tabs, and the
permalinks. #145/#146/#147 build on it.

What carries forward from ADR-0040 unchanged: one shared **ArtifactViewer** for
every surface that opens a file, and the `proxima:reveal-file` window-event
contract. Its *destination* decision is superseded. Between this record and #145
the only tree left in the destination was the read-only **Container inspection**
an Ops-migration recovery reveal asks for (`rootSide: 'container'`), rendered as
a transient panel with a Close action rather than a lens.

**Landed 2026-08-03 (#145):** decision 1 is executed. The dock's **Files** tool
is the file browser again; it absorbed that inspection panel, so the destination
now renders no tree at all, and `proxima:reveal-file` is answered by the dock.

**Landed 2026-08-03 (#146):** decision 3 is executed, and with it the shape of
opening anything. An artifact takes over the destination's **main window**
instead of a lightbox: markdown and text documents open **directly in the
editor** (the wiki markdown editor, or the file editor for other text) and
everything else in the **inline viewer**, which keeps its renderers and
neighbour walk and hands text-backed kinds to that editor through
**Edit source**. The popup is gone entirely - it was not kept for media either,
because a second way to look at a file is exactly the intermediate step this
decision removes. Data documents (CSV, JSON, Mermaid) stay with the viewer on
purpose: their rendering, not their bytes, is why the owner opened them. The two
surfaces were two-way at the time - the viewer's **Edit source** reached the
editor, the editor's **Review** reached the viewer's feedback pins - and the
second door has since been removed with those pins (see the #148 refinement
below). Both name their way back (**← Gallery** / **← Record**), and Delegate
behaves the same: it owns this destination, this editor, and this viewer.

**Landed 2026-08-03 (#147):** decision 4 is executed, and the decision set is
complete. The **running app renders in the main window** (`AppViewport`), full
width, with the device presets, Reload, and Open in new tab; the **Run controls
stay in the dock** (`AppRunner`) - folder, command, port, Run/Stop, the
owner-power consent, the logs, and every fail-closed refusal - keeping a compact
status (Ready/Starting, command, port) and one **Show app** action. Run opens the
viewport automatically, through the same shell seam a handed-off file uses
(#146). Three details sharpen this record:

- **The app is not an artifact.** It has no bytes, no record, and no neighbours
  to walk, so it is its own main-window surface rather than a fifth branch of the
  artifact router - and the two are exclusive: each closes the other. This is why
  the gallery still does not list runnable apps as cards; the entry point to a run
  is the Run controls, not a thumbnail whose click would have to start a process.
- **Only the location changed, not the boundary.** ADR-0042's app-frame model is
  restated in one module (`appPreview.ts`) and asserted in tests: an isolated
  origin (per-app subdomain or relay port) gets
  `allow-scripts allow-same-origin allow-forms allow-popups allow-modals`; the
  same-origin `/api/appview` fallback gets the same string **without**
  `allow-same-origin`, because that origin holds the owner's session. The consent
  gate, the origin selection, and the proxy semantics are untouched, and the
  backend was not modified.
- **Running an app is Work-only**, decided here rather than inherited - and it is
  a deliberate *removal*, not a relocation. Until now a deliverable record page
  would embed the whole runner in Delegate too, so an owner could start an
  owner-power dev server from the global mode. After this ticket the picture lives
  in a main window whose only way back to Stop is the dock, and Delegate has no
  dock: it would have the app and no controls. Rather than ship that, Delegate
  offers neither half - an app record's *Preview app* entry is absent there rather
  than dead, the rule #145 set for Reveal in Files. Delegate is already the mode
  without a terminal, without a file tree, and without a design canvas; owner-power
  execution belongs with them.

**Owner refinement 2026-08-03 (#148): the review panel is removed.** The viewer
inherited a fixed 22rem side column from the native review workspace (#32) - pin
count, **+ Pin**,
**Annotate**, a general-feedback field, and **Add feedback to chat**, which
resolved the artifact's producing session and seeded its composer with a
path-linked brief. It is gone, and with it the editor's **Review** action, whose
only job was to reach it. The reason is the one this ADR argues everywhere else:
*the widest surface in the app should show the work*. A review column took a third
of that surface from every artifact, and an HTML page - laid out for a viewport -
had to be scrolled sideways to be read at all, so the panel cost more than the
pins earned. Feedback on an artifact is written in Chat, where prompts are
written, with the artifact @-mentioned.

What this does NOT change: the renderers, the neighbour walk, **Edit source**, the
Mermaid whiteboard (which saves to `<mapped artifacts>/whiteboards/` exactly as
before), the active-preview consent boundary of ADR-0042, and the ledger. No API
route backed the review loop - it was browser-local state plus a composer draft -
so nothing was removed from the backend.

## Consequences

Positive:

- The widest surface in the app shows the work. A day of agent output is
  recognisable at a glance instead of read line by line.
- The ledger survives whole: badges, record panel, approvals (both doors),
  lineage, version chains, gone-file history - all backend untouched.
- The gallery is a live scan (`GET /api/projects/{slug}/artifacts`) and the
  ledger is a durable registry; keeping them as sibling tabs makes that
  distinction visible instead of hiding it behind one merged list.
- Delegate gets the same global gallery behind its head filter, the shape Tasks
  already has.

Negative / accepted trade-offs:

- **There was no full file browser between this ticket and #145.** Owners who
  reached arbitrary project files through the destination lost that path in the
  interim, and the record panel's "Reveal in Files" action stayed unwired for the
  same window. #145 closed both: the dock browser returned and that one call site
  now raises the reveal event (in Work only - Delegate has no dock, so the action
  is absent there rather than dead).
- The gallery only shows what the artifact scanner types and caps, so it is not
  a complete view of the folder; that completeness is the dock's job.
- Runnable apps are not gallery cards. #147 gave them the main-window viewport
  but deliberately not a thumbnail: a card whose click starts an owner-power
  process is not the same gesture as opening a file, so a run still begins at the
  Run controls.
- **Structured artifact feedback is gone with the review panel (#148).** Pinned
  point notes carried coordinates an agent could act on; a Chat message has to
  describe the same place in words, and there is no longer a one-click route from
  an artifact to the exact session that produced it. Accepted: the owner judged
  the width worth more than the structure, and the producing session is still
  reachable from the deliverable record's lineage.

## Related

- Supersedes: [`0040-files-is-a-destination.md`](0040-files-is-a-destination.md)
  (destination decision; its viewer and reveal-contract decisions stand)
- Shell information architecture: [`../ui-shell.md`](../ui-shell.md)
- Capabilities: [`../CAPABILITIES.md`](../CAPABILITIES.md)
- File preview boundary: [`0042-file-preview-is-a-sandboxed-iframe.md`](0042-file-preview-is-a-sandboxed-iframe.md)
