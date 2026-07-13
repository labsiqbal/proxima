import React from 'react'
import { ProximaMark } from '../components/brand/ProximaMark'
import { FolderLinker } from '../components/projects/FolderLinker'
import type { Project } from '../types'

// First-run step (right after setting a password): point Proxima at a real code
// folder to work in. Linking registers the folder as-is (files stay where they are);
// skipping just uses the starter project Proxima created under its data dir.
export function WorkspaceOnboarding({ token, onDone }: { token: string; onDone: (linked: Project | null) => void }) {
  const [busy, setBusy] = React.useState(false)
  return (
    <div className="center-screen">
      <div className="onboard-card">
        <ProximaMark className="proxima-mark-boot" label="Proxima" />
        <h1 className="auth-title">Pick your working folder</h1>
        <p className="auth-sub">
          Proxima works inside a project folder — your agents, chats, and terminal all
          operate on its files. Point it at your code (nothing is moved or copied), or
          skip to use the starter project.
        </p>
        <FolderLinker token={token} onLinked={async p => { onDone(p) }} />
        <button className="ghost-button onboard-skip" disabled={busy} onClick={() => { setBusy(true); onDone(null) }}>
          Skip for now
        </button>
      </div>
    </div>
  )
}
