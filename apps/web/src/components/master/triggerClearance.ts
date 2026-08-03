// The floating Master trigger and a surface's bottom-docked composer both want
// the bottom-right corner, and the composer wins: it is the primary action of
// the surface, and its Send button sits exactly where the trigger lands (#154).
//
// Rather than guess a clearance from a token (a composer grows with its text,
// its attachments, and its controls row), the trigger reads the dock it would
// cover and rises above it. The number below is a *clearance*: how far the
// trigger's bottom edge must sit above the viewport floor. CSS takes the larger
// of it and the resting offset, so a surface with no dock is untouched.

/** The parts of a DOMRect this calculation needs — so it is testable without a DOM. */
export type ClearanceRect = {
  top: number
  bottom: number
  left: number
  right: number
  height: number
}

/** Marks an element the floating trigger must never cover. */
export const COMPOSER_DOCK_ATTRIBUTE = 'data-composer-dock'
export const COMPOSER_DOCK_SELECTOR = `[${COMPOSER_DOCK_ATTRIBUTE}]`

/** Room between the dock's top edge and the trigger, in px (matches --space-3). */
export const CLEARANCE_GAP = 12
/** How close to the viewport floor a dock has to end to count as docked, in px. */
export const DOCK_THRESHOLD = 24

export function masterTriggerClearance({
  trigger,
  docks,
  viewportHeight,
  gap = CLEARANCE_GAP,
  dockThreshold = DOCK_THRESHOLD,
}: {
  /** The trigger's current rect; null when it is not rendered. */
  trigger: ClearanceRect | null
  docks: ClearanceRect[]
  viewportHeight: number
  gap?: number
  dockThreshold?: number
}): number {
  if (!trigger || viewportHeight <= 0) return 0
  let clearance = 0
  for (const dock of docks) {
    // A collapsed or hidden dock covers nothing.
    if (dock.height <= 0) continue
    // Only a dock anchored to the bottom of the viewport is in the trigger's
    // way; one that merely scrolled past is not, and lifting for it would fling
    // the trigger into the middle of the screen.
    if (viewportHeight - dock.bottom > dockThreshold) continue
    // Different columns never collide: a chat panel on the left of a split
    // surface leaves the bottom-right corner free.
    if (dock.right <= trigger.left || dock.left >= trigger.right) continue
    clearance = Math.max(clearance, viewportHeight - dock.top + gap)
  }
  return clearance
}
