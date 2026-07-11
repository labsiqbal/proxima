import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const normalized = (id: string) => id.replace(/\\/g, '/')

function videoVendorChunk(rawId: string): string | undefined {
  const id = normalized(rawId)
  if (id.includes('/node_modules/mediabunny/dist/modules/src/isobmff/')) return 'video-mediabunny-isobmff'
  if (id.includes('/node_modules/mediabunny/dist/modules/src/matroska/') || id.includes('/node_modules/mediabunny/dist/modules/src/hls/') || id.includes('/node_modules/mediabunny/dist/modules/src/mpeg-ts/') || id.includes('/node_modules/mediabunny/dist/modules/src/ogg/')) return 'video-mediabunny-formats'
  if (id.includes('/node_modules/mediabunny/')) return 'video-mediabunny-core'
  if (id.includes('/node_modules/@babel/parser/')) return 'video-parser-babel'
  if (id.includes('/node_modules/recast/') || id.includes('/node_modules/ast-types/') || id.includes('/node_modules/esprima/') || id.includes('/node_modules/source-map/')) return 'video-parser-recast'
  if (id.includes('/node_modules/acorn/') || id.includes('/node_modules/acorn-walk/') || id.includes('/node_modules/magic-string/')) return 'video-parser-acorn'
  if (id.includes('/node_modules/@hyperframes/parsers/')) return 'video-hf-parsers'
  if (id.includes('/node_modules/@hyperframes/core/') || id.includes('/node_modules/@hyperframes/sdk/') || id.includes('/node_modules/@hyperframes/lint/')) return 'video-hf-core'
  if (id.includes('/node_modules/@phosphor-icons/react/')) return 'video-icons'
  if (id.includes('/node_modules/linkedom/') || id.includes('/node_modules/htmlparser2/') || id.includes('/node_modules/entities/') || id.includes('/node_modules/domhandler/') || id.includes('/node_modules/domutils/') || id.includes('/node_modules/css-select/') || id.includes('/node_modules/css-what/') || id.includes('/node_modules/nth-check/') || id.includes('/node_modules/dom-serializer/') || id.includes('/node_modules/cssom/')) return 'video-dom-parser'
  if (id.includes('/node_modules/marked/')) return 'video-markdown'
  if (id.includes('/node_modules/@codemirror/view/') || id.includes('/node_modules/@codemirror/state/') || id.includes('/node_modules/@codemirror/commands/')) return 'codemirror-core'
  if (id.includes('/node_modules/@codemirror/language/') || id.includes('/node_modules/@codemirror/autocomplete/') || id.includes('/node_modules/@codemirror/search/') || id.includes('/node_modules/@lezer/common/') || id.includes('/node_modules/@lezer/lr/') || id.includes('/node_modules/@lezer/highlight/')) return 'codemirror-language'
  if (id.includes('/node_modules/@codemirror/lang-') || id.includes('/node_modules/@lezer/markdown/') || id.includes('/node_modules/@lezer/javascript/') || id.includes('/node_modules/@lezer/html/') || id.includes('/node_modules/@lezer/css/') || id.includes('/node_modules/@codemirror/theme-one-dark/')) return 'codemirror-langs'
  if (id.includes('/src/vendor/video-studio/components/editor/')) {
    const file = id.split('/').pop() || ''
    if (file === 'SourceEditor.tsx' || file.startsWith('FileTree')) return 'video-editor-source'
    if (file === 'PropertyPanel.tsx' || file.startsWith('propertyPanel') || file.includes('Animation') || file.includes('Keyframe') || file.includes('Ease') || file.includes('ArcPath') || file === 'ComputedTweenNotice.tsx' || file === 'BorderRadiusEditor.tsx' || file === 'Transform3DCube.tsx') return 'video-editor-inspector'
    if (file.startsWith('domEdit') || file.startsWith('manual') || file.startsWith('motionPath') || file.startsWith('snap') || file === 'DomEditOverlay.tsx' || file === 'MarqueeOverlay.tsx' || file === 'OffCanvasIndicators.tsx' || file === 'GridOverlay.tsx' || file === 'SnapGuideOverlay.tsx' || file === 'SnapToolbar.tsx' || file === 'marqueeCommit.ts' || file === 'useDomEditOverlayGestures.ts' || file === 'useDomEditOverlayRects.ts' || file === 'useMotionPathData.ts') return 'video-editor-canvas'
    return 'video-editor-ui'
  }
  if (id.includes('/src/vendor/video-studio/player/')) return 'video-player'
  if (id.includes('/src/vendor/video-studio/captions/')) return 'video-captions'
  if (id.includes('/src/vendor/video-studio/hooks/')) return 'video-hooks'
  if (id.includes('/src/vendor/video-studio/')) return 'video-shell'
}

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: (moduleId: string) => videoVendorChunk(moduleId) ?? null,
              test: (moduleId: string) => videoVendorChunk(moduleId) != null,
              minSize: 0,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/auth': 'http://127.0.0.1:8765',
      '/health': 'http://127.0.0.1:8765'
    }
  }
})
