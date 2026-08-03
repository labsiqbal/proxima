import type { Ref } from 'react'
import type { Project } from '../../types'
import { IconChevronLeft, IconMenu, IconNewChat, IconPanelRight, IconSearch } from './icons'
import { ProjectSwitcher } from './ProjectSwitcher'
import { ShellModeSwitch, type ShellMode } from './ShellModeSwitch'

export function MobileTopbar({
  activeProject,
  projects = [],
  onSelectProject,
  token,
  onProjectRenamed,
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
  mode = 'work',
  delegateEnabled = false,
  onModeChange,
  showProject = false,
  toolsOpen = false,
  onToggleTools,
}: {
  activeProject: Project | null
  projects?: Project[]
  onSelectProject?: (project: Project) => void
  token?: string
  onProjectRenamed?: (project: Project) => void
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
  mode?: ShellMode
  delegateEnabled?: boolean
  onModeChange?: (mode: ShellMode) => void
  showProject?: boolean
  /**
   * The tool sheet's only entry point at phone width (#156): the rail that
   * carries it on desktop is not rendered down here, because a 46px lane is
   * 12% of a 390px screen and everything in it is also in the sheet's own tab
   * row. Absent while Project tools are suppressed.
   */
  toolsOpen?: boolean
  onToggleTools?: () => void
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
      {showProject && onSelectProject ? (
        <ProjectSwitcher
          projects={projects}
          activeProject={activeProject}
          onSelectProject={onSelectProject}
          token={token}
          onProjectRenamed={onProjectRenamed}
          locked={projectLocked}
          lockedReason={projectLockedReason}
          compact
        />
      ) : (
        onModeChange ? <ShellModeSwitch mode={mode} delegateEnabled={delegateEnabled} onChange={onModeChange} /> : <strong>Work</strong>
      )}
    </div>
    <div className="mobile-actions">
      {onToggleTools && <button
        className={`icon-button ${toolsOpen ? 'active' : ''}`}
        onClick={onToggleTools}
        aria-label="Toggle tool dock"
        aria-pressed={toolsOpen}
        title={toolsOpen ? 'Hide tools' : 'Show tools'}
      ><IconPanelRight size={18} /></button>}
      <button className="icon-button" onClick={onSearch} aria-label="Search" title="Search"><IconSearch size={18} /></button>
      {/* Compact blank-session control: desktop nav has no New chat row; Chat
          header still has one, but this stays reachable from any mobile view. */}
      <button className="icon-button" onClick={onNewChat} aria-label="New chat"><IconNewChat size={18} /></button>
    </div>
  </header>
}
