import { IconChevronLeft } from '../shell/icons'

// One consistent, lightweight back affordance used in every detail header
// (Activity, Workflows editor, Tasks, Design Studio) — a muted chevron + label
// that brightens on hover, instead of a heavy bordered pill per screen.
export function BackButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="back-btn" onClick={onClick} title={label === 'Back' ? 'Back' : `Back to ${label}`}>
      <IconChevronLeft size={16} />{label}
    </button>
  )
}
