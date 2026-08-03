import React from 'react'
import { projectFs } from '../../api/fsAdapter'
import type { FileTarget } from '../../types'
import { fileKind } from './fileKind'

// A document opened from Artifacts is opened IN THE EDITOR (ADR-0043 decision 3,
// #146). There is no read-only step in front of it: the owner clicks a report
// and can type. That is the whole point of this component - it picks which of
// the two editors the app already has should hold the bytes, and hands them the
// Files-API adapter and the way back out:
// - markdown → the wiki's markdown editor (`WikiNote`), edit-first, with its
//   Preview tab still one click away for reading the rendered document. Opened
//   from here it carries no wiki graph: an artifact anywhere in the Container
//   has no backlinks to show and no `[[wikilink]]` namespace to resolve
//   against - that context belongs to the Wiki destination.
// - anything else text → `FileEditor` (CodeMirror, language-aware, ⌘/Ctrl+S).
// Both editors already guard unsaved bytes on close, so the way back cannot
// silently drop an edit, and both take focus so "editable immediately" is true
// for the keyboard too.
//
// Media, data, and pages are NOT documents in this sense: they keep the viewer
// and its renderers, and reach this editor through the viewer's "Edit source"
// (see `fileKind.opensInEditor`). The door the other way was a "Review" action
// that existed only to reach the viewer's feedback pins; it went with them
// (#148), and markdown still reads rendered through the editor's own Preview.

const WikiNote = React.lazy(() => import('../wiki/WikiNote').then(m => ({ default: m.WikiNote })))
const FileEditor = React.lazy(() => import('../files/FileEditor').then(m => ({ default: m.FileEditor })))

export function DocumentEditor({ token, slug, path, target, backLabel = 'Back', onClose }: {
  token: string
  slug: string
  path: string
  /** Area-mapped ref when the document is not plain container-relative (#136/#138). */
  target?: FileTarget
  /** Where the way back leads - the surface this editor was opened from. */
  backLabel?: string
  onClose: () => void
}) {
  const fs = React.useMemo(() => projectFs(token, slug), [token, slug])
  const name = path.split('/').pop() || path
  const closeLabel = `← ${backLabel}`
  const closeTitle = `Back to ${backLabel.toLowerCase()}`

  return <section
    className="artifacts-doc"
    role="region"
    aria-label={`Editing ${name}`}
    data-testid="artifacts-document-editor"
  >
    <React.Suspense fallback={<p className="muted artifacts-doc-loading">Loading editor…</p>}>
      {fileKind(path) === 'markdown'
        ? <WikiNote
            fs={fs}
            path={path}
            target={target}
            defaultMode="edit"
            autoFocus
            closeLabel={closeLabel}
            closeTitle={closeTitle}
            onClose={onClose}
          />
        : <FileEditor
            fs={fs}
            write={fs.write}
            path={path}
            target={target}
            autoFocus
            closeLabel={closeLabel}
            closeTitle={closeTitle}
            onClose={onClose}
          />}
    </React.Suspense>
  </section>
}
