// The pure half of the changes-review surface (slice 4, T1): turn one unified
// git patch into per-file render models. No React, no fetch — unit-testable.

export type DiffLineKind = 'hunk' | 'add' | 'del' | 'ctx' | 'meta'
export type DiffLine = { kind: DiffLineKind; text: string }
export type FileDiff = { path: string; lines: DiffLine[] }

/** Strip git's a/ b/ quoting from a header path token. */
const headerPath = (token: string): string => {
  const raw = token.startsWith('"') && token.endsWith('"') ? token.slice(1, -1) : token
  return raw.replace(/^[ab]\//, '')
}

const lineKind = (line: string): DiffLineKind => {
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+++') || line.startsWith('---')) return 'meta'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  if (line.startsWith(' ') || line === '') return 'ctx'
  return 'meta' // index/mode/similarity/rename headers
}

/**
 * Split one unified patch (git diff --no-color, rename-aware) into per-file
 * sections. The path is the post-change path (`+++ b/…`), falling back to the
 * pre-change path for deletions (`+++ /dev/null`) and to the `diff --git`
 * header when a section has no hunks at all (e.g. a pure rename).
 */
export function parseUnifiedPatch(patch: string): FileDiff[] {
  const files: FileDiff[] = []
  let current: (FileDiff & { oldPath?: string; sawHunk?: boolean }) | null = null
  for (const line of patch.split('\n')) {
    if (line.startsWith('diff --git ')) {
      if (current) files.push({ path: current.path, lines: current.lines })
      // "diff --git a/old b/new" — the last token is the post-change path.
      const tokens = line.slice('diff --git '.length).split(' ')
      current = { path: headerPath(tokens[tokens.length - 1] ?? ''), lines: [] }
      continue
    }
    if (!current) continue // preamble before the first file header
    if (line.startsWith('+++ ') && !line.startsWith('+++ /dev/null')) {
      current.path = headerPath(line.slice(4).trim())
    } else if (line.startsWith('--- ') && !line.startsWith('--- /dev/null')) {
      current.oldPath = headerPath(line.slice(4).trim())
    } else if (line.startsWith('+++ /dev/null') && current.oldPath) {
      current.path = current.oldPath
    }
    current.lines.push({ kind: lineKind(line), text: line })
  }
  if (current) files.push({ path: current.path, lines: current.lines })
  return files.filter(file => file.lines.length > 0 || file.path)
}

/** Git's one-letter file status, in plain words for the review surface. */
export function fileStatusLabel(status: string): string {
  switch (status[0]) {
    case 'A': return 'added'
    case 'D': return 'removed'
    case 'R': return 'renamed'
    case 'C': return 'copied'
    default: return 'changed'
  }
}

/** The worktree lifecycle in plain words (UI copy stays jargon-free). */
export function worktreeStateLabel(status: string): string {
  switch (status) {
    case 'active': return 'in progress'
    case 'merging': return 'landing'
    case 'merged': return 'merged'
    case 'conflict': return 'needs attention'
    case 'discarded': return 'discarded'
    default: return status
  }
}
