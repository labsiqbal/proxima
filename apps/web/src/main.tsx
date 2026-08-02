import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { App } from './App'
import { registerServiceWorker } from './pwa'
import { initAppearance } from './theme'
import { ErrorBoundary } from './components/shell/ErrorBoundary'
import { AppErrorToasts } from './components/shell/AppErrorToasts'
import { installGlobalErrorHandlers } from './lib/errorSurface'

// Installed before the first render so a throw during boot is still reported.
installGlobalErrorHandlers()
initAppearance()
registerServiceWorker()
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
    {/* Outside the boundary: the error surface must outlive the tree it reports on. */}
    <AppErrorToasts />
  </React.StrictMode>,
)
