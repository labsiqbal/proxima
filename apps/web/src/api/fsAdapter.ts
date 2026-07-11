import * as files from './files'
import * as wiki from './wiki'
import type { FileEntry } from '../types'

// A uniform filesystem interface so the tree + editor can operate on any source
// (a project's files, a project's wiki subfolder, or the personal wiki).
export type FsAdapter = {
  list: (path: string) => Promise<{ entries: FileEntry[] }>
  read: (path: string) => Promise<{ content: string }>
  write: (path: string, content: string) => Promise<unknown>
  mkdir: (path: string) => Promise<unknown>
  rename: (from: string, to: string) => Promise<unknown>
  remove: (path: string) => Promise<unknown>
}

const join = (base: string, p: string) => (base ? (p ? `${base}/${p}` : base) : p)

export function projectFs(token: string, slug: string, base = ''): FsAdapter {
  return {
    list: p => files.listTree(token, slug, join(base, p)),
    read: p => files.readFile(token, slug, join(base, p)),
    write: (p, c) => files.writeFile(token, slug, join(base, p), c),
    mkdir: p => files.mkdir(token, slug, join(base, p)),
    rename: (f, t) => files.renamePath(token, slug, join(base, f), join(base, t)),
    remove: p => files.deletePath(token, slug, join(base, p))
  }
}

export function wikiFs(token: string): FsAdapter {
  return {
    list: p => wiki.listTree(token, p),
    read: p => wiki.readFile(token, p),
    write: (p, c) => wiki.writeFile(token, p, c),
    mkdir: p => wiki.mkdir(token, p),
    rename: (f, t) => wiki.renamePath(token, f, t),
    remove: p => wiki.deletePath(token, p)
  }
}
