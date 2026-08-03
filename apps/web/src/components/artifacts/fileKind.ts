// What a file path IS, decided once for the whole Artifacts destination.
//
// The viewer used to own these regexes privately, which was fine while every
// open landed in the same lightbox. Since #146 an open is a fork - a document
// goes straight to the editor in the main window, everything else to the inline
// viewer - so the answer has to be readable by the screen that routes and by
// the viewer that renders. One module, one taxonomy.

const IMAGE = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i
const VIDEO = /\.(mp4|webm|mov|m4v)$/i
const PDF = /\.pdf$/i
const HTML = /\.html?$/i
const MARKDOWN = /\.(md|markdown)$/i
const MERMAID = /\.(mmd|mermaid)$/i
const CSV = /\.(csv|tsv)$/i
const JSON_LIKE = /\.(json|excalidraw)$/i
const TEXT = /\.(txt|log|ya?ml|yml|xml|ini|conf|env|toml|py|js|ts|tsx|jsx|css|sh|sql|rs|go|rb|java|c|h|cpp)$/i

export type FileKind =
  | 'image'
  | 'video'
  | 'pdf'
  | 'html'
  | 'markdown'
  | 'mermaid'
  | 'csv'
  | 'json'
  | 'text'
  | 'binary'

// A name with nothing to read an extension from: `README`, `LICENSE`,
// `Dockerfile`, `.gitignore` - and also every folder-shaped path.
const EXTENSIONLESS = /^\.?[^.]*$/

export function fileKind(path: string): FileKind {
  if (IMAGE.test(path)) return 'image'
  if (VIDEO.test(path)) return 'video'
  if (PDF.test(path)) return 'pdf'
  if (HTML.test(path)) return 'html'
  if (MARKDOWN.test(path)) return 'markdown'
  if (MERMAID.test(path)) return 'mermaid'
  if (CSV.test(path)) return 'csv'
  if (JSON_LIKE.test(path)) return 'json'
  if (TEXT.test(path)) return 'text'
  return 'binary'
}

/** Kinds whose bytes are text, so "Edit source" can hand them to the editor. */
export const EDITABLE_KINDS: ReadonlySet<FileKind> = new Set<FileKind>([
  'markdown',
  'mermaid',
  'csv',
  'json',
  'text',
  'html',
])

/**
 * Does opening this artifact land in the editor rather than the viewer? (#146)
 *
 * A **document you write** - prose, notes, source - has no rendering worth an
 * intermediate step: the editor IS the point, so the owner types immediately.
 * A **document you read as data or a picture** keeps the renderer that makes it
 * legible (CSV table, JSON tree, Mermaid diagram, sandboxed HTML page, image,
 * video) and reaches the editor from there through "Edit source".
 *
 * Types whose path is a FOLDER rather than a file - a design scene, a runnable
 * app - are never text, whatever their extensionless path looks like.
 */
export const opensInEditor = (artifact: { type?: string; path: string }): boolean => {
  if (artifact.type === 'design' || artifact.type === 'app') return false
  const kind = fileKind(artifact.path)
  if (kind === 'markdown' || kind === 'text') return true
  // `README`, `LICENSE`, `Dockerfile`: documents with no extension to read, and
  // the editor is the only surface that can do anything with them. The viewer
  // still calls them binary - a folder-shaped path reaches IT, never this - and
  // a genuinely binary file is refused by the Files API with a readable reason
  // rather than rendered as mojibake.
  return kind === 'binary' && EXTENSIONLESS.test(artifact.path.split('/').pop() || '')
}
