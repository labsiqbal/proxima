import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { IconCopy, IconCheck, IconFile } from '../shell/icons'
import {
  fileUrl,
  isSvgPath,
  rawUrl,
  relativeFileUrl,
  relativeRawUrl,
  resolveRelativeReference,
  retargetFile,
} from '../../api/files'
import { useRawBlobUrl } from '../../hooks/useRawBlobUrl'
import type { FileTarget } from '../../types'

// A project-relative path (e.g. artifacts/x.png) vs an absolute/external URL.
const isRel = (u?: string) => !!u && !/^(https?:|data:|blob:|mailto:|#|\/)/i.test(u)
const fileName = (p: string) => { try { return decodeURIComponent(p.split('/').pop() || p) } catch { return p } }

function ProjectSvgImage({
  token,
  slug,
  path,
  target,
  alt,
  className,
}: {
  token: string
  slug: string
  path: string
  target?: FileTarget
  alt: string
  className?: string
}) {
  const src = useRawBlobUrl(token, slug, path, target)
  if (!src) return null
  return <img className={className} src={src} alt={alt} />
}

// A fenced code block with a copy button. The copy reads the rendered text
// straight off the <pre>, so it works regardless of language/highlighting.
function CodeBlock({ children }: { children?: React.ReactNode }) {
  const ref = React.useRef<HTMLPreElement>(null)
  const [copied, setCopied] = React.useState(false)
  const mountedRef = React.useRef(true)
  const resetTimer = React.useRef<number | null>(null)
  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      if (resetTimer.current != null) window.clearTimeout(resetTimer.current)
    }
  }, [])
  const copy = async () => {
    const text = ref.current?.innerText ?? ''
    try {
      await navigator.clipboard.writeText(text)
      if (!mountedRef.current) return
      setCopied(true)
      if (resetTimer.current != null) window.clearTimeout(resetTimer.current)
      resetTimer.current = window.setTimeout(() => {
        if (mountedRef.current) setCopied(false)
      }, 1400)
    } catch { /* clipboard unavailable */ }
  }
  return (
    <div className="code-block">
      <button className={`copy-btn ${copied ? 'copied' : ''}`} onClick={copy} title="Copy code" aria-label="Copy code">{copied ? <IconCheck size={14} /> : <IconCopy size={14} />}<span>{copied ? 'Copied' : 'Copy'}</span></button>
      <pre ref={ref}>{children}</pre>
    </div>
  )
}

// Renders assistant/streaming text as GitHub-flavored markdown. react-markdown
// tolerates partial markdown during streaming (an unclosed code fence renders
// progressively as a code block), so it is safe to feed in-flight deltas.
function MessageContentInner({ content, token, slug, sourcePath, fileTarget }: {
  content: string
  token?: string
  slug?: string
  sourcePath?: string
  fileTarget?: FileTarget
}) {
  const canResolve = !!token && !!slug
  const previewResourceUrl = (reference: string) => sourcePath
    ? relativeFileUrl(slug!, reference, sourcePath, fileTarget)
    : fileUrl(slug!, reference)
  const downloadResourceUrl = (reference: string) => sourcePath
    ? relativeRawUrl(slug!, reference, sourcePath, fileTarget)
    : rawUrl(slug!, reference)
  const resolvedProjectPath = (reference: string) => {
    if (sourcePath) return resolveRelativeReference(reference, sourcePath)
    return reference.split(/[?#]/, 1)[0] || reference
  }
  const components: React.ComponentProps<typeof ReactMarkdown>['components'] = {
    pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
    // Inline images stored in the project (e.g. an attachment or generated chart).
    img: ({ src, alt }) => {
      const s = typeof src === 'string' ? src : ''
      if (canResolve && isRel(s)) {
        const resolved = resolvedProjectPath(s)
        if (!resolved) return null
        if (isSvgPath(resolved)) {
          const target = fileTarget
            ? retargetFile(fileTarget, resolved)
            : undefined
          return <ProjectSvgImage
            token={token!}
            slug={slug!}
            path={resolved}
            target={target}
            alt={alt || ''}
            className="md-img"
          />
        }
        return <img className="md-img" src={previewResourceUrl(s)} alt={alt || ''} />
      }
      return <img className="md-img" src={s} alt={alt || ''} />
    },
    // Links to project files become download chips; external links stay normal.
    a: ({ href, children }) => {
      const h = typeof href === 'string' ? href : ''
      if (canResolve && isRel(h)) {
        const hrefUrl = downloadResourceUrl(h)
        if (!hrefUrl) return <span>{children}</span>
        return <a className="file-chip" href={hrefUrl} download={fileName(h)} target="_blank" rel="noreferrer"><IconFile size={15} /><span>{fileName(h)}</span></a>
      }
      return <a href={h} target="_blank" rel="noreferrer">{children}</a>
    }
  }
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

// Memoized: markdown parsing is heavy, and during streaming the thread re-renders
// ~30×/s — without this, every completed message re-parses each tick (choppy).
export const MessageContent = React.memo(MessageContentInner)
