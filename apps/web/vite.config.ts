import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const normalized = (id: string) => id.replace(/\\/g, '/')
const apiTarget = `http://127.0.0.1:${process.env.PROXIMA_PORT || '8765'}`

function editorVendorChunk(rawId: string): string | undefined {
  const id = normalized(rawId)
  if (id.includes('/node_modules/@codemirror/view/') || id.includes('/node_modules/@codemirror/state/') || id.includes('/node_modules/@codemirror/commands/')) return 'codemirror-core'
  if (id.includes('/node_modules/@codemirror/language/') || id.includes('/node_modules/@codemirror/autocomplete/') || id.includes('/node_modules/@codemirror/search/') || id.includes('/node_modules/@lezer/common/') || id.includes('/node_modules/@lezer/lr/') || id.includes('/node_modules/@lezer/highlight/')) return 'codemirror-language'
  if (id.includes('/node_modules/@codemirror/lang-') || id.includes('/node_modules/@lezer/markdown/') || id.includes('/node_modules/@lezer/javascript/') || id.includes('/node_modules/@lezer/html/') || id.includes('/node_modules/@lezer/css/') || id.includes('/node_modules/@codemirror/theme-one-dark/')) return 'codemirror-langs'
}

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: (moduleId: string) => editorVendorChunk(moduleId) ?? null,
              test: (moduleId: string) => editorVendorChunk(moduleId) != null,
              minSize: 0,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      // ws:true so the terminal WebSocket (/api/ws/terminal) upgrade is forwarded to
      // the backend in dev. The string shorthand only proxies HTTP, which left the
      // terminal stuck "connecting" behind the vite dev server.
      '/api': { target: apiTarget, ws: true },
      '/auth': apiTarget,
      '/health': apiTarget
    }
  }
})
