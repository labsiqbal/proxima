import { describe, expect, it } from "vitest";
import {
	applyThreadScrollFollow,
	CHAT_SCROLL_FIT_SLACK,
	CHAT_SCROLL_PIN_SLACK,
	isThreadPinnedNearBottom,
	shouldFollowThreadBottom,
	threadContentFitsViewport,
} from "./threadScroll";

describe("threadContentFitsViewport", () => {
	it("treats equal heights as fitting", () => {
		expect(threadContentFitsViewport(400, 400)).toBe(true);
	});

	it("treats short content as fitting", () => {
		expect(threadContentFitsViewport(200, 500)).toBe(true);
	});

	it("allows a few pixels of overflow slack before counting as overflow", () => {
		expect(
			threadContentFitsViewport(400 + CHAT_SCROLL_FIT_SLACK, 400),
		).toBe(true);
		expect(
			threadContentFitsViewport(400 + CHAT_SCROLL_FIT_SLACK + 1, 400),
		).toBe(false);
	});
});

describe("shouldFollowThreadBottom", () => {
	it("never follows when unpinned", () => {
		expect(shouldFollowThreadBottom(2000, 400, false)).toBe(false);
	});

	it("does not force bottom on short content even when pinned", () => {
		expect(shouldFollowThreadBottom(200, 500, true)).toBe(false);
		expect(shouldFollowThreadBottom(500, 500, true)).toBe(false);
	});

	it("follows when pinned and content overflows the viewport", () => {
		expect(shouldFollowThreadBottom(1200, 400, true)).toBe(true);
	});
});

describe("applyThreadScrollFollow", () => {
	it("leaves short content at scrollTop 0 (top-anchored)", () => {
		const el = { scrollHeight: 240, clientHeight: 600, scrollTop: 0 };
		expect(applyThreadScrollFollow(el, true)).toBe(false);
		expect(el.scrollTop).toBe(0);
	});

	it("does not jump short content that already has a scrollTop", () => {
		const el = { scrollHeight: 240, clientHeight: 600, scrollTop: 12 };
		expect(applyThreadScrollFollow(el, true)).toBe(false);
		expect(el.scrollTop).toBe(12);
	});

	it("pins overflowing content to the bottom when pinned", () => {
		const el = { scrollHeight: 2000, clientHeight: 500, scrollTop: 0 };
		expect(applyThreadScrollFollow(el, true)).toBe(true);
		expect(el.scrollTop).toBe(2000);
	});

	it("does not move overflowing content when unpinned", () => {
		const el = { scrollHeight: 2000, clientHeight: 500, scrollTop: 80 };
		expect(applyThreadScrollFollow(el, false)).toBe(false);
		expect(el.scrollTop).toBe(80);
	});
});

describe("isThreadPinnedNearBottom", () => {
	it("is true within the pin slack of the bottom", () => {
		const scrollHeight = 1000;
		const clientHeight = 400;
		const nearBottomTop = scrollHeight - clientHeight - (CHAT_SCROLL_PIN_SLACK - 1);
		expect(
			isThreadPinnedNearBottom(scrollHeight, nearBottomTop, clientHeight),
		).toBe(true);
	});

	it("is false when the user has scrolled well above the bottom", () => {
		expect(isThreadPinnedNearBottom(1000, 0, 400)).toBe(false);
	});
});
