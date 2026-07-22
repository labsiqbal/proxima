import { describe, expect, it } from 'vitest'
import { fileStatusLabel, parseUnifiedPatch, worktreeStateLabel } from './diff'

const PATCH = [
  'diff --git a/README.md b/README.md',
  'index 1234567..89abcde 100644',
  '--- a/README.md',
  '+++ b/README.md',
  '@@ -1,2 +1,3 @@',
  ' hello',
  '-old line',
  '+new line',
  '+world',
  'diff --git a/src/new.py b/src/new.py',
  'new file mode 100644',
  'index 0000000..f00ba44',
  '--- /dev/null',
  '+++ b/src/new.py',
  '@@ -0,0 +1 @@',
  "+print('hi')",
  '',
].join('\n')

describe('parseUnifiedPatch', () => {
  it('splits a patch into per-file sections with classified lines', () => {
    const files = parseUnifiedPatch(PATCH)
    expect(files.map(file => file.path)).toEqual(['README.md', 'src/new.py'])

    const readme = files[0]
    const kinds = readme.lines.map(line => line.kind)
    expect(kinds).toContain('hunk')
    expect(readme.lines.find(line => line.kind === 'add')?.text).toBe('+new line')
    expect(readme.lines.find(line => line.kind === 'del')?.text).toBe('-old line')
    expect(readme.lines.find(line => line.text === ' hello')?.kind).toBe('ctx')
    // File headers are meta, not adds/dels — '+++'/'---' must never color as changes.
    expect(readme.lines.find(line => line.text.startsWith('+++'))?.kind).toBe('meta')
    expect(readme.lines.find(line => line.text.startsWith('---'))?.kind).toBe('meta')
  })

  it('uses the pre-change path for deletions', () => {
    const files = parseUnifiedPatch([
      'diff --git a/gone.txt b/gone.txt',
      'deleted file mode 100644',
      '--- a/gone.txt',
      '+++ /dev/null',
      '@@ -1 +0,0 @@',
      '-bye',
    ].join('\n'))
    expect(files).toHaveLength(1)
    expect(files[0].path).toBe('gone.txt')
  })

  it('takes the post-change path for renames', () => {
    const files = parseUnifiedPatch([
      'diff --git a/old-name.txt b/new-name.txt',
      'similarity index 100%',
      'rename from old-name.txt',
      'rename to new-name.txt',
    ].join('\n'))
    expect(files).toHaveLength(1)
    expect(files[0].path).toBe('new-name.txt')
  })

  it('returns nothing for an empty patch', () => {
    expect(parseUnifiedPatch('')).toEqual([])
  })
})

describe('plain-words labels', () => {
  it('maps git file statuses to words', () => {
    expect(fileStatusLabel('A')).toBe('added')
    expect(fileStatusLabel('M')).toBe('changed')
    expect(fileStatusLabel('D')).toBe('removed')
    expect(fileStatusLabel('R100')).toBe('renamed')
  })

  it('maps the isolated-copy lifecycle to words (no git jargon on the surface)', () => {
    expect(worktreeStateLabel('active')).toBe('in progress')
    expect(worktreeStateLabel('merged')).toBe('merged')
    expect(worktreeStateLabel('conflict')).toBe('needs attention')
    expect(worktreeStateLabel('discarded')).toBe('discarded')
  })
})
