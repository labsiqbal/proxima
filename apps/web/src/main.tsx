import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { App } from './App'
import { registerServiceWorker } from './pwa'
import { initAppearance } from './theme'
import { ErrorBoundary } from './components/shell/ErrorBoundary'

initAppearance()
registerServiceWorker()
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
