import * as files from './files'
import type { FileEntry } from '../types'
import type { FileRef } from './files'

// A uniform filesystem interface so the tree + editor can operate on any source
// (a project's files, a project's wiki subfolder, or the personal wiki).
export type ReadOnlyFsAdapter = {
  list: (ref: FileRef) => Promise<{ entries: FileEntry[] }>
  read: (ref: FileRef) => Promise<{ content: string }>
}

export type FsAdapter = ReadOnlyFsAdapter & {
  write: (ref: FileRef, content: string) => Promise<unknown>
  mkdir: (ref: FileRef) => Promise<unknown>
  rename: (from: FileRef, to: FileRef) => Promise<unknown>
  remove: (ref: FileRef) => Promise<unknown>
}

const join = (base: string, p: string) => (base ? (p ? `${base}/${p}` : base) : p)
const scoped = (base: string, ref: FileRef): FileRef =>
  typeof ref === 'string' ? join(base, ref) : ref

export function projectFs(token: string, slug: string, base = ''): FsAdapter {
  return {
    list: ref => files.listTree(token, slug, scoped(base, ref)),
    read: ref => files.readFile(token, slug, scoped(base, ref)),
    write: (ref, content) => files.writeFile(token, slug, scoped(base, ref), content),
    mkdir: ref => files.mkdir(token, slug, scoped(base, ref)),
    rename: (from, to) => files.renamePath(token, slug, scoped(base, from), scoped(base, to)),
    remove: ref => files.deletePath(token, slug, scoped(base, ref)),
  }
}

export function containerInspectionFs(
  token: string,
  slug: string,
): ReadOnlyFsAdapter {
  return {
    list: path => files.listTree(token, slug, path, 'container'),
    read: path => files.readFile(token, slug, path, 'container'),
  }
}
