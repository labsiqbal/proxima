import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { ArtifactViewer } from './ArtifactViewer'
import { previewUrl, rawUrl } from '../../api/files'

const fsRead = vi.fn()
const setTargetPreviewMode = vi.fn()
const fetchRawBlobMock = vi.fn()

vi.mock('../../api/files', () => ({
  previewUrl: vi.fn((_slug: string, path: string, _target?: unknown, active?: { generation: string }) => `/preview/${path}${active ? `?generation=${active.generation}` : ''}`),
  rawUrl: vi.fn((slug: string, path: string, target?: { path?: string }) => (
    target
      ? `/api/projects/${slug}/raw?target=${encodeURIComponent(JSON.stringify(target))}`
      : `/api/projects/${slug}/raw?path=${encodeURIComponent(path)}`
  )),
  isSvgPath: (path: string) => /\.svg$/i.test(path),
  fetchRawBlob: (...args: unknown[]) => fetchRawBlobMock(...args),
  setTargetPreviewMode: (...args: unknown[]) => setTargetPreviewMode(...args),
}))
vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({ read: (...args: unknown[]) => fsRead(...args) })),
}))
vi.mock('../chat/MessageContent', () => ({
  MessageContent: ({ content, sourcePath, fileTarget }: {
    content: string
    sourcePath?: string
    fileTarget?: unknown
  }) => <div
    data-testid="message-content"
    data-source-path={sourcePath}
    data-file-target={JSON.stringify(fileTarget)}
  >{content}</div>,
}))
vi.mock('./MermaidDiagram', () => ({
  MermaidDiagram: ({ source, onEdit }: { source: string; onEdit: () => void }) => <button type="button" onClick={onEdit}>Edit diagram {source}</button>,
}))
vi.mock('./ExcalidrawWhiteboard', () => ({
  ExcalidrawWhiteboard: ({ onClose }: { onClose: () => void }) => <div data-testid="whiteboard">
    <button type="button" onClick={onClose}>Back to artifact</button>
  </div>,
}))

beforeEach(() => {
  fsRead.mockReset()
  setTargetPreviewMode.mockReset()
  fetchRawBlobMock.mockReset()
  fetchRawBlobMock.mockResolvedValue('blob:svg-preview')
  window.localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ArtifactViewer', () => {
  // #146: the viewer is the MAIN WINDOW, not a lightbox over it. No dialog role,
  // no portal, no scrim, no focus trap - and Escape belongs to whatever overlay
  // is genuinely on top (the dock panel, a confirm dialog), never to this.
  // Focus enters on the way back; returning it to the trigger belongs to the
  // opener, which outlives this surface (`ArtifactsScreen`).
  it('renders in the main window with a named way back, not as a popup', async () => {
    function Harness() {
      const [open, setOpen] = React.useState(false)
      return <>
        <button type="button" onClick={() => setOpen(true)}>Open artifact</button>
        {open && <ArtifactViewer
          token="token"
          slug="master"
          items={[{ type: 'image', title: 'Hero', path: 'artifacts/hero.png' }]}
          index={0}
          onIndex={() => undefined}
          backLabel="Gallery"
          onClose={() => setOpen(false)}
        />}
      </>
    }

    const view = render(<Harness />)
    await userEvent.click(view.getByRole('button', { name: 'Open artifact' }))

    const surface = screen.getByRole('region', { name: 'Artifact: Hero' })
    expect(surface).not.toHaveAttribute('aria-modal')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(surface.parentElement).not.toBe(document.body)

    const back = screen.getByRole('button', { name: 'Back to gallery' })
    await waitFor(() => expect(back).toHaveFocus())

    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('region', { name: 'Artifact: Hero' })).toBeInTheDocument()

    await userEvent.click(back)
    expect(screen.queryByRole('region', { name: 'Artifact: Hero' })).not.toBeInTheDocument()
  })

  // #148: the owner removed the review side panel outright. The artifact is the
  // whole surface now - no pins, no annotate mode, no general-feedback field, and
  // no handoff into chat - so a page renders at the window's width instead of
  // inside a two-thirds column that had to be scrolled sideways to read.
  it('gives the whole surface to the artifact - no review panel beside it', () => {
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'image', title: 'Hero', path: 'artifacts/hero.png' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(screen.queryByRole('complementary', { name: 'Artifact feedback' })).not.toBeInTheDocument()
    expect(screen.queryByText(/pins?$/i)).not.toBeInTheDocument()
    for (const name of ['+ Pin', 'Annotate', 'Add feedback to chat']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('textbox', { name: 'General feedback' })).not.toBeInTheDocument()
    expect(document.querySelector('.av-annotation-layer')).toBeNull()
    // The stage is the only child under the bar, so it can be full width.
    expect(document.querySelector('.av-review-panel')).toBeNull()
    expect(document.querySelector('.av-workspace')).toBeNull()
    expect(document.querySelector('.av-stage')).toBeInTheDocument()
  })

  // An HTML page is laid out for a viewport, so it fills the stage edge to edge
  // rather than sitting in a centred, capped column (#148).
  it('lets a page fill the stage instead of a centred column', () => {
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'file', title: 'Terms', path: 'site/terms.html' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(document.querySelector('.av-stage')).toHaveClass('fill')
    expect(document.querySelector('iframe.av-frame')).toBeInTheDocument()
  })

  it('keeps the padded, centred stage for documents and pictures', async () => {
    fsRead.mockResolvedValue({ content: '# Report' })
    const { rerender } = render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'doc', title: 'Report', path: 'reports/report.md' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    await waitFor(() => expect(screen.getByTestId('message-content')).toBeInTheDocument())
    expect(document.querySelector('.av-stage')).not.toHaveClass('fill')

    // A design's artboard is a picture, not a page: pinned to the chrome it reads
    // worse than centred, so it keeps the padding an image gets.
    fsRead.mockResolvedValue({ content: JSON.stringify({
      artboards: [{ id: 'a', width: 1080, height: 1080, background: '#fff', layers: [] }],
    }) })
    rerender(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'design', title: 'Poster', path: 'artifacts/design/poster' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)
    await waitFor(() => expect(document.querySelector('.av-design')).toBeInTheDocument())
    expect(document.querySelector('.av-stage')).not.toHaveClass('fill')
  })

  // A design has no bytes to preview; without its artboard the stage would be an
  // unsupported-file dead end, which is exactly what Delegate would show (#146).
  it('draws a design from its artboard instead of the unsupported fallback', async () => {
    fsRead.mockResolvedValue({ content: JSON.stringify({
      artboards: [{ id: 'a', width: 1080, height: 1080, background: '#fff', layers: [] }],
    }) })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'design', title: 'Poster', path: 'artifacts/design/poster' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)
    await waitFor(() => expect(document.querySelector('.av-design')).toBeInTheDocument())
    expect(screen.queryByText(/Can't preview this file type/)).not.toBeInTheDocument()
    // A folder has nothing to download either.
    expect(screen.queryByRole('link', { name: 'Download' })).not.toBeInTheDocument()
  })

  // The way from a rendered document to its bytes: data and pages keep their
  // renderer, so "Edit source" is how they reach the same main-window editor a
  // markdown or text document opens in directly (#146).
  it('hands editable kinds to the editor through Edit source', async () => {
    const onEditSource = vi.fn()
    fsRead.mockResolvedValue({ content: 'a,b\n1,2\n' })
    const item = { type: 'file' as const, title: 'rows.csv', path: 'exports/rows.csv' }
    const { rerender } = render(<ArtifactViewer
      token="token"
      slug="master"
      items={[item]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
      onEditSource={onEditSource}
    />)
    await userEvent.click(await screen.findByRole('button', { name: 'Edit source' }))
    expect(onEditSource).toHaveBeenCalledWith(item)

    const binary = { type: 'file' as const, title: 'tool.wasm', path: 'bin/tool.wasm' }
    rerender(<ArtifactViewer
      token="token"
      slug="master"
      items={[binary]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
      onEditSource={onEditSource}
    />)
    expect(screen.queryByRole('button', { name: 'Edit source' })).not.toBeInTheDocument()
  })

  // A stale artifact is still readable: the error belongs on the stage, and the
  // walk to its neighbours keeps working.
  it('reports an unreadable artifact on the stage without losing the way back', async () => {
    fsRead.mockRejectedValue(new Error('gone'))
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'doc', title: 'Old report', path: 'reports/gone.md' }]}
      index={0}
      onIndex={() => undefined}
      backLabel="Gallery"
      onClose={() => undefined}
    />)

    expect(await screen.findByText('Could not read this file.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to gallery' })).toBeInTheDocument()
  })

  it('uses the artifact target for text and media resolution instead of its display path', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'brief.md',
    }
    fsRead.mockResolvedValue({ content: '# Ops brief' })
    const item = {
      type: 'doc',
      title: 'brief.md',
      path: 'brief.md',
      target,
    } as Parameters<typeof ArtifactViewer>[0]['items'][number]
    const { unmount } = render(<ArtifactViewer
      token="token"
      slug="master"
      items={[item]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(await screen.findByText('# Ops brief')).toBeInTheDocument()
    expect(fsRead).toHaveBeenCalledWith(target)
    unmount()

    const image = {
      ...item,
      type: 'image',
      title: 'visual.png',
      path: 'visual.png',
      target: { ...target, path: 'visual.png' },
    } as Parameters<typeof ArtifactViewer>[0]['items'][number]
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[image]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(previewUrl).toHaveBeenCalledWith('master', 'visual.png', image.target)
  })

  it('passes the originating Area and document path to Markdown resources', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'reports/brief.md',
    }
    fsRead.mockResolvedValue({ content: '![Chart](images/chart.png)' })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'doc', title: 'Brief', path: 'brief.md', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    const markdown = await screen.findByTestId('message-content')
    expect(markdown).toHaveAttribute('data-source-path', 'reports/brief.md')
    expect(JSON.parse(markdown.getAttribute('data-file-target') || '{}')).toEqual(target)
  })

  it('renders targeted HTML passive and script-free by default', () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'site/index.html',
    }
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Site', path: 'site/index.html', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(screen.getByTitle('index.html')).toHaveAttribute('sandbox', '')
    expect(screen.getByText('Passive preview')).toBeInTheDocument()
    expect(previewUrl).toHaveBeenCalledWith(
      'master',
      'site/index.html',
      target,
      undefined,
    )
  })

  it('keeps legacy HTML passive without offering unscoped active mode', () => {
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Legacy', path: 'legacy.html' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    const frame = screen
      .getAllByTitle('legacy.html')
      .find(node => node.tagName === 'IFRAME')
    expect(frame).toHaveAttribute('sandbox', '')
    expect(screen.queryByRole('button', { name: 'Enable active preview' })).not.toBeInTheDocument()
  })

  it('offers active preview on an insecure origin, where crypto.randomUUID does not exist', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'site/index.html',
    }
    const secure = globalThis.crypto
    vi.stubGlobal('crypto', {
      getRandomValues: (array: Uint8Array) => secure.getRandomValues(array),
    })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Site', path: 'site/index.html', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    // A plain-HTTP tailnet origin is an insecure context: only randomUUID is
    // missing, and the viewer id must not depend on it.
    expect(screen.getByRole('button', { name: 'Enable active preview' })).toBeEnabled()
  })

  it('requires explicit trust consent and revokes active mode back to passive', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'site/index.html',
    }
    setTargetPreviewMode
      .mockResolvedValueOnce({ active: true })
      .mockResolvedValueOnce({ active: false })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Site', path: 'site/index.html', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    await userEvent.click(screen.getByRole('button', { name: 'Enable active preview' }))
    const consent = screen.getByRole('alertdialog', { name: 'Enable trusted active content?' })
    expect(consent).toHaveTextContent('run scripts and module workers')
    expect(consent).toHaveTextContent('send any data from this Area to external services')
    expect(consent).toHaveTextContent('cannot guarantee Area confidentiality')
    expect(consent).toHaveTextContent('sandbox with no access to Proxima itself')
    expect(setTargetPreviewMode).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Enable trusted active mode' }))
    await waitFor(() => expect(screen.getByText('Active preview')).toBeInTheDocument())
    // Scripts, never the Proxima origin: the sandbox stays opaque in both modes.
    expect(screen.getByTitle('index.html')).toHaveAttribute('sandbox', 'allow-scripts')
    expect(setTargetPreviewMode).toHaveBeenNthCalledWith(
      1,
      'token',
      'master',
      target,
      expect.stringMatching(/^[A-Za-z0-9_-]{32,128}$/),
      true,
    )
    expect(previewUrl).toHaveBeenLastCalledWith(
      'master',
      'site/index.html',
      target,
      expect.objectContaining({
        previewSession: expect.stringMatching(/^[A-Za-z0-9_-]{32,128}$/),
      }),
    )

    await userEvent.click(screen.getByRole('button', { name: 'Disable active preview' }))
    await waitFor(() => expect(screen.getByText('Passive preview')).toBeInTheDocument())
    expect(screen.getByTitle('index.html')).toHaveAttribute('sandbox', '')
    expect(setTargetPreviewMode).toHaveBeenNthCalledWith(
      2,
      'token',
      'master',
      target,
      expect.stringMatching(/^[A-Za-z0-9_-]{32,128}$/),
      false,
    )
  })

  it('shows an actionable fallback instead of loading forever for a directory or unknown binary', () => {
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'app', title: 'Starter app', path: 'artifacts/starter-app' }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
    expect(screen.getByText(/Can't preview this file type/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Download' })).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          href: expect.stringContaining('/api/projects/master/raw?path=artifacts%2Fstarter-app'),
        }),
      ]),
    )
    expect(fsRead).not.toHaveBeenCalled()
  })

  it('opens a Mermaid block as an editable whiteboard and returns to the artifact', async () => {
    fsRead.mockResolvedValue({ content: '# Flow\n\n```mermaid\ngraph LR\n A-->B\n```' })
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'doc', title: 'Flow', path: 'reports/flow.md' }]}
      index={0}
      onIndex={() => undefined}
      backLabel="Gallery"
      onClose={() => undefined}
    />)

    const edit = await screen.findByRole('button', { name: /Edit diagram graph LR/ })
    await userEvent.click(edit)
    expect(await screen.findByTestId('whiteboard')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Back to artifact' }))

    await waitFor(() => expect(screen.queryByTestId('whiteboard')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Back to gallery' })).toBeInTheDocument()
  })

  it('downloads active media through the authenticated raw endpoint', () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'site/index.html',
    }
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'page', title: 'Site', path: 'site/index.html', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    const download = screen.getByRole('link', { name: 'Download' })
    expect(download).toHaveAttribute(
      'href',
      `/api/projects/master/raw?target=${encodeURIComponent(JSON.stringify(target))}`,
    )
    expect(download).toHaveAttribute('download', 'index.html')
    expect(rawUrl).toHaveBeenCalledWith('master', 'site/index.html', target)
    expect(previewUrl).toHaveBeenCalledWith(
      'master',
      'site/index.html',
      target,
      undefined,
    )
    expect(String(download.getAttribute('href'))).not.toContain('/api/target-preview/')
    expect(String(download.getAttribute('href'))).not.toContain('/preview/')
  })

  it('renders SVG through authenticated raw bytes instead of the preview entry', async () => {
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'brand/logo.svg',
    }
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'image', title: 'Logo', path: 'brand/logo.svg', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    const image = await screen.findByRole('img', { name: 'logo.svg' })
    expect(image).toHaveAttribute('src', 'blob:svg-preview')
    expect(fetchRawBlobMock).toHaveBeenCalledWith('token', 'master', 'brand/logo.svg', target)
    expect(previewUrl).not.toHaveBeenCalledWith('master', 'brand/logo.svg', target)

    const download = screen.getByRole('link', { name: 'Download' })
    expect(download).toHaveAttribute(
      'href',
      `/api/projects/master/raw?target=${encodeURIComponent(JSON.stringify(target))}`,
    )
    expect(download).toHaveAttribute('download', 'logo.svg')
  })

  it('surfaces a retryable error when SVG raw bytes fail to load', async () => {
    fetchRawBlobMock.mockRejectedValue(new Error('missing'))
    const target = {
      project: 'master',
      area: { kind: 'ops', id: 42 },
      path: 'brand/logo.svg',
    }
    render(<ArtifactViewer
      token="token"
      slug="master"
      items={[{ type: 'image', title: 'Logo', path: 'brand/logo.svg', target }]}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
    />)

    expect(await screen.findByText(/Could not load this image/i)).toBeInTheDocument()
    expect(screen.queryByText('Loading...')).not.toBeInTheDocument()

    fetchRawBlobMock.mockResolvedValueOnce('blob:svg-retry')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    const image = await screen.findByRole('img', { name: 'logo.svg' })
    expect(image).toHaveAttribute('src', 'blob:svg-retry')
    expect(fetchRawBlobMock).toHaveBeenCalledTimes(2)
  })
})
