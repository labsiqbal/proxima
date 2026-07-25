/** Near-bottom pin threshold (px) used by the thread scroll listener. */
export const CHAT_SCROLL_PIN_SLACK = 80;

/**
 * Layout slack (px): treat content as "fits the viewport" when only a few
 * pixels of overflow remain, so short threads stay top-anchored instead of
 * jittering around the scroll boundary.
 */
export const CHAT_SCROLL_FIT_SLACK = 8;

/**
 * True when the thread content fits (or nearly fits) the scrollport.
 * Short conversations must stay top-anchored — no forced bottom jump.
 */
export function threadContentFitsViewport(
	scrollHeight: number,
	clientHeight: number,
	fitSlack: number = CHAT_SCROLL_FIT_SLACK,
): boolean {
	return scrollHeight <= clientHeight + fitSlack;
}

/**
 * Whether auto-scroll should pin the scrollport to the latest content.
 *
 * - Unpinned (user scrolled up): never follow.
 * - Content fits the viewport: never force bottom (stay top).
 * - Overflowing + pinned: follow so streaming / open lands on latest.
 */
export function shouldFollowThreadBottom(
	scrollHeight: number,
	clientHeight: number,
	pinned: boolean,
	fitSlack: number = CHAT_SCROLL_FIT_SLACK,
): boolean {
	if (!pinned) return false;
	if (threadContentFitsViewport(scrollHeight, clientHeight, fitSlack)) {
		return false;
	}
	return true;
}

/** Minimal scroll element shape for tests and the layout effect. */
export type ThreadScrollEl = {
	scrollHeight: number;
	clientHeight: number;
	scrollTop: number;
};

/**
 * Apply pin-to-latest when appropriate. Leaves short threads at scrollTop 0
 * (top-anchored). Returns whether a bottom jump was applied.
 */
export function applyThreadScrollFollow(
	el: ThreadScrollEl,
	pinned: boolean,
	fitSlack: number = CHAT_SCROLL_FIT_SLACK,
): boolean {
	if (
		!shouldFollowThreadBottom(
			el.scrollHeight,
			el.clientHeight,
			pinned,
			fitSlack,
		)
	) {
		return false;
	}
	el.scrollTop = el.scrollHeight;
	return true;
}

/**
 * Whether the user is near the bottom of the scrollport (pin / at-bottom UI).
 */
export function isThreadPinnedNearBottom(
	scrollHeight: number,
	scrollTop: number,
	clientHeight: number,
	pinSlack: number = CHAT_SCROLL_PIN_SLACK,
): boolean {
	return scrollHeight - scrollTop - clientHeight < pinSlack;
}
