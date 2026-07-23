import type { Ref } from 'react'
import type { Project } from '../../types'
import { IconMenu, IconNewChat, IconSearch } from './icons'

export function MobileTopbar({
  activeProject,
  drawerOpen = false,
  onMenu,
  onSearch,
  onNewChat,
  menuButtonRef,
}: {
  activeProject: Project | null
  drawerOpen?: boolean
  onMenu: () => void
  onSearch: () => void
  onNewChat: () => void
  menuButtonRef?: Ref<HTMLButtonElement>
}) {
  return <header className="mobile-topbar">
    <button
      ref={menuButtonRef}
      className="icon-button"
      onClick={onMenu}
      aria-label="Menu"
      aria-expanded={drawerOpen}
      aria-controls="mobile-nav-drawer"
    >
      <IconMenu size={18} />
    </button>
    <div className="mobile-context"><strong>{activeProject?.name || 'Proxima'}</strong></div>
    <div className="mobile-actions">
      <button className="icon-button" onClick={onSearch} aria-label="Search" title="Search"><IconSearch size={18} /></button>
      <button className="icon-button" onClick={onNewChat} aria-label="New chat"><IconNewChat size={18} /></button>
    </div>
  </header>
}
