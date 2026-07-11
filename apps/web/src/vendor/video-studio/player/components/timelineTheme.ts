import type { TimelineElement } from "../store/playerStore";

export interface TimelineTrackStyle {
  clip: string;
  accent: string;
  label: string;
  iconBackground: string;
}

export interface TimelineTheme {
  shellBackground: string;
  shellBorder: string;
  rulerBorder: string;
  rowBackground: string;
  rowBorder: string;
  gutterBackground: string;
  gutterBorder: string;
  textPrimary: string;
  textSecondary: string;
  tickText: string;
  tickMajor: string;
  tickMinor: string;
  clipBackground: string;
  clipBackgroundActive: string;
  clipBorder: string;
  clipBorderHover: string;
  clipBorderActive: string;
  clipShadow: string;
  clipShadowHover: string;
  clipShadowActive: string;
  clipShadowDragging: string;
  handleColor: string;
  panelResizeSeam: string;
  panelResizeActive: string;
  clipRadius: string;
}

const TRACK_STYLE: TimelineTrackStyle = {
  clip: "color-mix(in srgb, var(--ui-accent) 7%, var(--proxima-video-surface))",
  accent: "var(--proxima-video-accent-strong, var(--ui-accent))",
  label: "var(--proxima-video-text, #111827)",
  iconBackground: "color-mix(in srgb, var(--ui-accent) 13%, var(--proxima-video-surface))",
};

export const defaultTimelineTheme: TimelineTheme = {
  shellBackground: "var(--proxima-video-timeline-bg, var(--proxima-video-surface, #ffffff))",
  shellBorder: "var(--proxima-video-border-soft, rgba(15,23,42,0.10))",
  rulerBorder: "var(--proxima-video-border-soft, rgba(15,23,42,0.08))",
  rowBackground: "var(--proxima-video-timeline-row, var(--proxima-video-surface, #ffffff))",
  rowBorder: "var(--proxima-video-border-soft, rgba(15,23,42,0.08))",
  gutterBackground: "var(--proxima-video-timeline-gutter, var(--proxima-video-surface-soft, #f8fafc))",
  gutterBorder: "var(--proxima-video-border-soft, rgba(15,23,42,0.10))",
  textPrimary: "var(--proxima-video-text, #111827)",
  textSecondary: "var(--proxima-video-muted, #64748b)",
  tickText: "var(--proxima-video-faint, #94a3b8)",
  tickMajor: "color-mix(in srgb, var(--proxima-video-text, #111827) 20%, transparent)",
  tickMinor: "color-mix(in srgb, var(--proxima-video-text, #111827) 10%, transparent)",
  clipBackground: "var(--proxima-video-timeline-clip, #ffffff)",
  clipBackgroundActive: "color-mix(in srgb, var(--ui-accent) 10%, var(--proxima-video-surface))",
  clipBorder: "var(--proxima-video-border, rgba(15,23,42,0.14))",
  clipBorderHover: "color-mix(in srgb, var(--ui-accent) 35%, var(--proxima-video-border))",
  clipBorderActive: "var(--proxima-video-accent-strong, var(--ui-accent))",
  clipShadow: "0 1px 2px rgba(15,23,42,0.06)",
  clipShadowHover: "0 5px 14px rgba(15,23,42,0.12)",
  clipShadowActive: "0 6px 18px rgba(15,23,42,0.14), 0 0 0 1px color-mix(in srgb, var(--ui-accent) 18%, transparent)",
  clipShadowDragging: "0 14px 32px rgba(15,23,42,0.22), 0 0 0 1px color-mix(in srgb, var(--ui-accent) 26%, transparent)",
  handleColor: "color-mix(in srgb, var(--proxima-video-text, #111827) 28%, transparent)",
  panelResizeSeam: "var(--proxima-video-border-soft, rgba(15,23,42,0.10))",
  panelResizeActive: "color-mix(in srgb, var(--ui-accent) 28%, transparent)",
  clipRadius: "6px",
};

export function getTimelineTrackStyle(_tag: string): TimelineTrackStyle {
  return TRACK_STYLE;
}

export function getClipHandleOpacity({
  isHovered,
  isSelected,
  isDragging,
}: {
  isHovered: boolean;
  isSelected: boolean;
  isDragging: boolean;
}): number {
  if (isDragging) return 0.95;
  if (isSelected) return 0.82;
  if (isHovered) return 0.76;
  return 0;
}

export function getRenderedTimelineElement({
  element,
  draggedElementId,
  previewStart,
  previewTrack,
}: {
  element: TimelineElement;
  draggedElementId: string | null;
  previewStart: number | null;
  previewTrack: number | null;
}): TimelineElement {
  if (
    (element.key ?? element.id) !== draggedElementId ||
    previewStart === null ||
    previewTrack === null
  ) {
    return element;
  }
  return {
    ...element,
    start: previewStart,
    track: previewTrack,
  };
}
