# Lavish Export Playbook

The package doubles as a **presentation**. lavish-axi owns the visual layer: interactive review during the gates and the final portable artifact.

- `masterplan.md` → `masterplan.html` (via `lavish-axi export`)
- `VERDICT.md` → `VERDICT.html` (same treatment, on a false-premise stop)

The Markdown stays the single source of truth — the HTML is a render of it, regenerated whenever it changes (revise mode step 6). Never hand-edit the export as the master.

## The flow

1. **Author the artifact HTML** driven by the masterplan's §15 Design direction — lavish injects no design system, so the deck previews the product's own look, not a generic theme.
2. **Open it for review:** `lavish-axi <file>` — the reviewer reads, annotates, and sketches on it during Gates A/B/C.
3. **Collect feedback:** `lavish-axi poll` — annotations come back as prompts; whiteboard edits come back as a whiteboard prompt.
4. **Ship:** `lavish-axi export` writes the portable self-contained HTML next to the Markdown; `lavish-axi share` optionally produces a link.

## Diagrams

Author diagrams as **Mermaid** in `.mermaid` containers so lavish converts them to **editable Excalidraw whiteboards** — a reviewer redraws the flow instead of describing the change. Every type the template uses (flowchart, erDiagram, sequenceDiagram) converts.

## Without lavish

On a runtime without lavish-axi, skip this file entirely: the text package is complete on its own, and Mermaid sources render natively on GitHub and most Markdown viewers. Rich HTML export is simply unavailable there — say so honestly rather than hand-rolling a substitute pipeline.
