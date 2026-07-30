export type ShellMode = 'work' | 'delegate'

export function ShellModeSwitch({ mode, delegateEnabled, onChange }: {
  mode: ShellMode
  delegateEnabled: boolean
  onChange: (mode: ShellMode) => void
}) {
  return <div className="shell-mode-switch" role="group" aria-label="Workspace mode">
    <button type="button" className={mode === 'work' ? 'active' : ''} aria-pressed={mode === 'work'} onClick={() => onChange('work')}>Work</button>
    {delegateEnabled && <button type="button" className={mode === 'delegate' ? 'active' : ''} aria-pressed={mode === 'delegate'} onClick={() => onChange('delegate')}>Delegate</button>}
  </div>
}
