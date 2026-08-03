import { describe, expect, it } from 'vitest'
import { EDITABLE_KINDS, fileKind, opensInEditor } from './fileKind'

describe('fileKind', () => {
  it('names what a path is, by extension', () => {
    expect(fileKind('artifacts/hero.png')).toBe('image')
    expect(fileKind('artifacts/cut.mp4')).toBe('video')
    expect(fileKind('reports/plan.pdf')).toBe('pdf')
    expect(fileKind('exports/index.html')).toBe('html')
    expect(fileKind('reports/plan.md')).toBe('markdown')
    expect(fileKind('docs/flow.mmd')).toBe('mermaid')
    expect(fileKind('exports/rows.csv')).toBe('csv')
    expect(fileKind('exports/scene.json')).toBe('json')
    expect(fileKind('src/main.py')).toBe('text')
    expect(fileKind('bin/tool.wasm')).toBe('binary')
    // Nothing to read an extension from - which includes every folder path, so
    // the viewer keeps its immediate download fallback for them.
    expect(fileKind('README')).toBe('binary')
    expect(fileKind('artifacts/design/poster')).toBe('binary')
  })

  it('keeps every text kind editable at source', () => {
    for (const kind of ['markdown', 'mermaid', 'csv', 'json', 'text', 'html'] as const) {
      expect(EDITABLE_KINDS.has(kind)).toBe(true)
    }
    expect(EDITABLE_KINDS.has('image')).toBe(false)
    expect(EDITABLE_KINDS.has('binary')).toBe(false)
  })

  // #146: a document you write goes straight to the editor. Data and media keep
  // their renderer (table, tree, diagram, picture) and reach the editor through
  // the viewer's "Edit source".
  it('sends documents you write to the editor, and everything else to the viewer', () => {
    expect(opensInEditor({ path: 'reports/plan.md' })).toBe(true)
    expect(opensInEditor({ path: 'wiki/index.markdown' })).toBe(true)
    expect(opensInEditor({ path: 'notes.txt' })).toBe(true)
    expect(opensInEditor({ path: 'scripts/build.sh' })).toBe(true)
    // Documents with no extension to read still belong in the editor: it is the
    // only surface that can do anything with them.
    expect(opensInEditor({ path: 'LICENSE' })).toBe(true)
    expect(opensInEditor({ path: 'ops/Dockerfile' })).toBe(true)
    expect(opensInEditor({ path: '.gitignore' })).toBe(true)

    expect(opensInEditor({ path: 'artifacts/hero.png' })).toBe(false)
    expect(opensInEditor({ path: 'artifacts/cut.mp4' })).toBe(false)
    expect(opensInEditor({ path: 'exports/index.html' })).toBe(false)
    expect(opensInEditor({ path: 'exports/rows.csv' })).toBe(false)
    expect(opensInEditor({ path: 'exports/scene.json' })).toBe(false)
    expect(opensInEditor({ path: 'docs/flow.mmd' })).toBe(false)
    expect(opensInEditor({ path: 'bin/tool.wasm' })).toBe(false)
    // A folder-shaped artifact is never text, extensionless path or not.
    expect(opensInEditor({ type: 'design', path: 'artifacts/design/poster' })).toBe(false)
    expect(opensInEditor({ type: 'app', path: 'site' })).toBe(false)
  })
})
