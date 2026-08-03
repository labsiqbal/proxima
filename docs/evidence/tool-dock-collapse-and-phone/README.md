# Right tool dock: collapse control, no rail on a phone, one close button

Real-browser captures for **#160**, **#156** and **#161**, taken against a
**disposable** instance: its own database, own `HOME`, own workspace root and own
project tree under `/tmp`. The live owner instance and every linked real folder were
untouched, and the fixture was removed after the run.

Desktop shots are `1600x1000`, phone shots `390x844`, each in the **Light** and
**Dark** presets. Console was clean (zero messages of type error) across the pass.

## 1. #156 - no tool rail at phone width

The rail took 46px of a 390px screen, permanently, out of the surface behind it: the
Design Studio canvas ended at the rail's edge. It is not rendered at phone width any
more, and nothing is stranded - the three tools are the sheet's own tab row, Settings
is in the drawer's account menu, and the sheet is opened from the mobile top bar's
tool control (the header control's glyph, one bar down).

| | Before | After |
| --- | --- | --- |
| Light | [before](before-390-design-light.png) | [after](after-390-design-light.png) |
| Dark | [before](before-390-design-dark.png) | [after](after-390-design-dark.png) |

Measured at 390px, same page, before → after:

| | Before | After |
| --- | --- | --- |
| `--toolrail-w` | `46px` | `0px` |
| `.tool-rail` display / width | `flex` / 46px | `none` / 0 |
| `.main-pane` width | 357px | **390px** |
| `.tool-panel` position / width | `absolute` / 289px | `fixed` / **390px** |

The sheet itself, opened from the top bar's tool control (pressed state on), on the
tool last used:

| Light | Dark |
| --- | --- |
| [sheet](after-390-sheet-light.png) | [sheet](after-390-sheet-dark.png) |

**A correction to #154's evidence.** That ticket's row 4 says the phone panel is "a
sheet flush against the rail". It never was: those rules sat in the mobile block near
the top of `styles.css`, *before* `.tool-rail` / `.tool-panel`, and a media query adds
no specificity - so they lost, and at 390px the "sheet" was still the 289px desktop
column with a rail beside it (the Before column above). The dock's phone rules now sit
immediately after the dock's own section, and a stylesheet test locks that order.

## 2. #160 - collapse the dock from the header

The sidebar toggle's mirrored twin sits next to it, so both edges of the shell are put
away from the same place. Collapsing zeroes `--toolrail-w`, and every width derived
from it follows.

| | Expanded | Collapsed |
| --- | --- | --- |
| Light | [expanded](after-1600-expanded-light.png) | [collapsed](after-1600-collapsed-light.png) |
| Dark | [expanded](after-1600-expanded-dark.png) | [collapsed](after-1600-collapsed-dark.png) |

Measured at 1600px: expanded `--toolrail-w: 46px`, rail 46px, main pane 1260px →
collapsed `--toolrail-w: 0px`, rail hidden, main pane **1306px**, preference stored as
`proxima.dockCollapsed=1`.

Behaviour checked live, not only in tests:

- A `proxima:reveal-file` raised **while collapsed** opens Files *and brings the rail
  back* (`dock-collapsed` gone, token back to 46px, panel open). It is a preference,
  not a suppression.
- Collapsing closes an open panel; latched terminals keep running behind it.
- The phone sheet is retired when the window widens to the desktop layout - without
  that, the rail refused to collapse because an invisible phone flag was still set.
- The sheet's own ✕ and Escape both close it and release the top bar's toggle;
  reopening returns to the tool last used (Files, above).

## 3. #161 - one close affordance per panel

The Preview tool stacked two ✕ on the same edge: the dock's tab row and the Run &
Preview header right below it. The tab row owns closing, the same as for Terminal and
Files.

| | Before (two ✕) | After (one ✕) |
| --- | --- | --- |
| Light | [before](before-1600-preview-light.png) | [after](after-1600-preview-light.png) |
| Dark | [before](before-1600-preview-dark.png) | [after](after-1600-preview-dark.png) |

Enumerated in the live DOM, panel closers by accessible name:
`Close tool panel, Close` → `Close tool panel`.

## Recipe

1. Disposable instance: own `HOME`, `PROXIMA_DB_PATH`, `PROXIMA_WORKSPACE_ROOT` and
   `PROXIMA_HERMES_PROFILES_ROOT` under `/tmp`, API on an unused port, `npm run dev`
   pointed at it. Set the owner password through `/auth/set-password`, create one
   fixture project, skip the core tour.
2. `before` shots: `git checkout <base> -- apps/web/src/styles.css apps/web/src/components/shell/{ToolDock,AppShell,MobileTopbar,icons}.tsx apps/web/src/components/files/AppRunner.tsx`,
   capture, then `git checkout HEAD -- <same files>`. Real code both times, same page,
   no injected CSS.
3. Themes are switched by setting `proxima.theme` and the `data-theme` attribute, the
   same two things the appearance picker writes.
