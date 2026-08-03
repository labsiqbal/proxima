# The artifact toolbar's way back: before/after evidence

Real-browser captures for #159, taken against a **disposable** instance: its own
database, own `HOME`, own workspace and own link root under `/tmp`. The live
owner instance and every linked real folder were untouched; the fixture was
deleted after the run.

## The convention this aligns to

A back affordance leads its row everywhere else in Proxima:

| Surface | Control | Position |
| --- | --- | --- |
| Shell chrome (desktop top bar, mobile topbar) | chevron **Back** | first, after the sidebar toggle |
| Detail headers (Design Studio gallery, and `ui/BackButton`'s other callers) | chevron + label | first |
| Artifact viewer | **← Gallery** / **← Record** | first in `.av-bar` |
| App viewport | **← Gallery** | first in `.app-viewport-bar` |
| **Artifact editor (before this change)** | **← Gallery** / **← Record** / **← Artifact** | **last** |

The editor was the only one out of line, because both editors render that
control as their pane `Close` - and a Close does belong last.

## Before / after

Desktop shots are 1440x900; phone shots are 390x844.

| Surface | Before | After |
| --- | --- | --- |
| Markdown document (wiki editor), desktop | [`release-plan` \| Edit/Preview \| Save \| ← Gallery](before-editor-markdown-desktop.png) | [← Gallery \| `release-plan` \| Edit/Preview \| Save](after-editor-markdown-desktop.png) |
| Markdown document, 390px | [same order, back last](before-editor-markdown-390.png) | [back first, title ellipsised, one row](after-editor-markdown-390.png) |
| Text document (CodeMirror editor), desktop | [`notes.txt` \| Save \| ← Gallery](before-editor-text-desktop.png) | [← Gallery \| `notes.txt` \| Save](after-editor-text-desktop.png) |
| Viewer (CSV), desktop | unchanged | [← Gallery already led the bar](after-viewer-desktop.png) |
| Viewer, 390px | unchanged | [bar wraps, back still leads the first row](after-viewer-390.png) |

Measured after the change, from the live DOM (`getBoundingClientRect().left` of
each head control, 1440px): `← Artifact@306 | Save@1335`. The accessibility tree
confirms DOM order too, so the change holds for the keyboard and a screen
reader, not only for the eye: the editor region's first control is
`button "Back to gallery"`, followed by `Edit`, `Preview`, `Save`.

## What did NOT move

A pane's **Close** is not a way back and stays with the trailing actions - the
Wiki destination's note pane and the dock's inspection file editor are
pixel-identical. That distinction is the `closeAs` prop, and both halves are
asserted in `DocumentEditor.test.tsx` and `WikiNote.test.tsx`.

## Behaviour re-checked in the browser

Placement only, so every path was walked again on the after build: gallery →
markdown editor → `← Gallery` → gallery; dock file → text editor; viewer →
`Edit source` → editor labelled `← Artifact` → back to the **viewer** →
`← Gallery` → gallery. Labels, `aria-label`s, and the unsaved-bytes guard are
untouched.

## Recipe

1. `npm --prefix apps/web run build`.
2. Start a throwaway server with `HOME`, `PROXIMA_DB_PATH`, `PROXIMA_WORKSPACE_ROOT`,
   `PROXIMA_LINK_ROOTS` and `TMPDIR` all pointing inside one `/tmp` directory
   (the same environment `scripts/verify_master_browser.py` builds), then
   `POST /auth/set-password` and `POST /api/projects/link` a fixture folder
   holding `docs/release-plan.md`, `docs/notes.txt` and `docs/diagram.csv`.
3. Log in, open **Artifacts**, and capture the three surfaces at 1440x900 and
   390x844. The text document and the CSV are reached through the dock's Files
   tree; the gallery lists the markdown one directly.
4. Delete the `/tmp` fixture directory and stop the server.
