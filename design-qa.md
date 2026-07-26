# Workflows Home Design QA

**Comparison target**

- Source visual truth: `/tmp/claude-1001/-home-iqbal-firstmate/426e52bf-eadf-4358-95a7-8b0f499406c6/scratchpad/proxima-workflow-redesign.html`
- Source capture: `/tmp/proxima-wf-mock-section-1.png`
- Desktop implementation: `/tmp/proxima-wf-home-desktop-final.png`
- Mobile implementation: `/tmp/proxima-wf-home-mobile-list-2.png`
- Desktop schedule dialog: `/tmp/proxima-wf-home-schedule.png`
- Mobile schedule dialog: `/tmp/proxima-wf-home-mobile.png`
- Full-view comparison: `/tmp/proxima-wf-design-compare.png`
- Focused table comparison: `/tmp/proxima-wf-design-focused-compare.png`

**Capture normalization**

- Desktop source and implementation are both 1440 x 1100 pixels at a 1440 x 1100 CSS viewport and device scale factor 1.
- The focused comparison crops the Workflows tab and both workflow groups from each desktop capture, then scales each side proportionally into a 720 x 325 pixel comparison panel.
- Mobile implementation is 390 x 844 pixels at a 390 x 844 CSS viewport and device scale factor 1.
- State: authenticated Workflows home, Workflows tab selected, two Manual rows and two Scheduled rows. The dialog captures use the scheduled Artikel Harian workflow.
- The source is a dark design study while the implementation intentionally uses Proxima's active light theme and existing tokens, as required by the product brief.

**Findings**

- No actionable P0, P1, or P2 differences remain.
- The information architecture matches the source: one tab row, independent Drafts / Workflows / Runs tables, two visually distinct workflow groups, category and trigger chips, and right-aligned actions.
- The implementation preserves the app shell and current theme instead of reproducing the mockup's presentation frame. Group density, row rhythm, control hierarchy, and table alignment remain visibly equivalent.
- The manual rows include a Schedule action beyond the mockup's Edit and Run actions. This is an intentional capability-preserving addition: after removing the standalone schedule view, it is the entry point for creating a workflow's first schedule.

**Required fidelity surfaces**

- Fonts and typography: Existing Proxima font tokens, weights, hierarchy, labels, truncation, and small-text treatment are consistent with the surrounding product UI and reproduce the mockup's hierarchy.
- Spacing and layout rhythm: Tabs, toolbar, group headers, table tracks, row padding, radii, and vertical gaps align cleanly at desktop. Mobile rows collapse to labeled fields without horizontal overflow.
- Colors and visual tokens: All new styling uses the centralized Proxima design tokens. Manual and Scheduled groups remain distinguishable through subtle accent and warning token mixes in both supported theme families.
- Image quality and asset fidelity: The target contains no raster imagery. Existing app icons are reused; no placeholder or generated assets are required.
- Copy and content: Draft, workflow, run, category, trigger, cadence, pause/resume, schedule, and duration labels match the approved flow. Existing schedule-form copy and capabilities are preserved inside the dialog.

**Primary interactions tested**

- Switched among Drafts, Workflows, and Runs.
- Reloaded the page and confirmed the last selected tab is restored.
- Opened the schedule dialog from a scheduled workflow and confirmed the existing cadence, overlap, input, enabled, Run now, and delete controls render.
- Closed the dialog and checked both desktop and mobile layouts.
- Confirmed no document or Workflows-home horizontal overflow at 1440 and 390 CSS pixels.
- Reloaded while authenticated and confirmed the browser console has no errors.

**Comparison history**

1. Initial desktop comparison found the table header cells did not use the same grid tracks as their rows. The header received the shared row class, and `/tmp/proxima-wf-home-desktop-final.png` confirms aligned headers and cells.
2. Initial mobile capture `/tmp/proxima-wf-home-mobile-list.png` found a P2 responsive issue: the hidden table-header rule was overridden by the mobile row-grid rule, so labels appeared as an empty stacked record. The responsive CSS order was corrected. `/tmp/proxima-wf-home-mobile-list-2.png` confirms the header is hidden, each real row keeps its field labels, and the page has no horizontal overflow.
3. The post-fix focused comparison `/tmp/proxima-wf-design-focused-compare.png` confirms the final desktop table composition and action hierarchy against the source.

**Implementation Checklist**

- [x] Match approved tab and grouped-table structure.
- [x] Preserve schedule creation and management in a per-row dialog.
- [x] Verify desktop and mobile interaction states.
- [x] Resolve all P0, P1, and P2 visual findings.

**Follow-up Polish**

- Friendly localized labels for arbitrary custom cron strings can be considered separately; raw custom cron remains accurate and matches the existing scheduling behavior.

final result: passed
