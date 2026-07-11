// ── Design Panel Tokens (for inline style={{}} usage) ──────────────────
// Tailwind classes use `panel-*` from tailwind.config.js theme.extend.colors.
// This file provides the same values for inline styles where Tailwind can't reach.

export const P = {
  accent: "var(--proxima-video-accent-strong, #0053fd)",
  borderInput: "var(--proxima-video-border, #cbd5e1)",
  textMuted: "var(--proxima-video-icon-muted, #64748b)",
  white: "var(--proxima-video-icon, #111827)",
} as const;
