import type { Ref } from 'react'
import type { Project } from '../../types'
import { IconChevronLeft, IconMenu, IconNewChat, IconSearch } from './icons'
import { ProjectSwitcher } from './ProjectSwitcher'

export function MobileTopbar({
  activeProject,
  projects = [],
  onSelectProject,
  projectLocked = false,
  projectLockedReason,
  chromeBackEnabled = false,
  chromeBackLabel = 'Back',
  onChromeBack,
  drawerOpen = false,
  onMenu,
  onSearch,
  onNewChat,
  menuButtonRef,
}: {
  activeProject: Project | null
  projects?: Project[]
  onSelectProject?: (project: Project) => void
  projectLocked?: boolean
  projectLockedReason?: string
  chromeBackEnabled?: boolean
  chromeBackLabel?: string
  onChromeBack?: () => void
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
    <button
      type="button"
      className="icon-button chrome-back-btn"
      disabled={!chromeBackEnabled}
      onClick={() => onChromeBack?.()}
      aria-label={chromeBackEnabled ? chromeBackLabel : 'Back'}
      title={chromeBackEnabled ? chromeBackLabel : 'Back'}
    >
      <IconChevronLeft size={18} />
    </button>
    <div className="mobile-context">
      {onSelectProject ? (
        <ProjectSwitcher
          projects={projects}
          activeProject={activeProject}
          onSelectProject={onSelectProject}
          locked={projectLocked}
          lockedReason={projectLockedReason}
          compact
        />
      ) : (
        <strong>{activeProject?.name || 'Proxima'}</strong>
      )}
    </div>
    <div className="mobile-actions">
      <button className="icon-button" onClick={onSearch} aria-label="Search" title="Search"><IconSearch size={18} /></button>
      {/* Compact blank-session control: desktop nav has no New chat row; Chat
          header still has one, but this stays reachable from any mobile view. */}
      <button className="icon-button" onClick={onNewChat} aria-label="New chat"><IconNewChat size={18} /></button>
    </div>
  </header>
}
