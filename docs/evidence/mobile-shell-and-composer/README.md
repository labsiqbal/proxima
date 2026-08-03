# Phone-width shell, Delegate collapse parity, and the floating Master trigger

Real-browser captures for #154, taken against a **disposable** instance: its own
database, own `HOME`, own workspace and own link root under `/tmp`. The live owner
instance and every linked real folder were untouched; the fixture was removed after
the run.

Phone shots are `390x844` at 2x device pixels (the width the ticket names).
Desktop shots are `1280x900` and `1600x1000` at 1x.

## 1. Delegate sidebar collapse parity

Work had the collapse toggle; Delegate had none - even though Delegate's column was
already being sized by Work's drag handle through the same `--left-w`. Collapse and
resize are now properties of the sidebar, not of a mode, and Delegate collapses to
the same #153 rail: three equal square tiles, one gutter, labels back as tooltips.

| Before (no control) | After (rail) | After (dark) |
| --- | --- | --- |
| [before](before-desktop-delegate-no-collapse.png) | [after](after-desktop-delegate-collapsed.png) | [after dark](after-desktop-delegate-collapsed-dark.png) |

Collapsing also stopped taking the **main window's** eyebrows with it: the rail's
hide list matched the app-wide `.eyebrow` class unscoped, so "CHAT" and "YOUR
ORCHESTRATOR" vanished whenever the sidebar happened to be collapsed.

## 2. Phone-width inventory (~390px, both modes)

| # | Surface | What was broken | Fix | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Mobile top bar, both modes | The status cluster (`1 running` / Needs-you) was a fixed overlay pinned over the bar: it covered **Search** entirely. | The phone shell is a grid - bar in one cell, cluster in an auto-sized cell beside it that collapses to nothing when empty. | [before](before-phone-work-chat.png) / [after](after-phone-work-chat.png) |
| 2 | Navigation drawer | The same overlay floated **above the open drawer**, covering the drawer's own Close button. | Same fix, plus the cluster drops its desktop z-index so the drawer covers the bar. | [before](before-phone-drawer.png) / [after](after-phone-drawer.png) |
| 3 | Chat composer, both modes and desktop | The floating Master trigger sat **on top of the Send button**. | The trigger measures the dock it would cover and rises above it (see §3). | [before](before-phone-work-chat.png) / [after](after-phone-work-chat.png) |
| 4 | Tool dock panel | The surface behind reserved **74% of the pane** for the panel, crushing the screen into a one-word-per-line strip; the panel left a 16px sliver; its three labelled tabs pushed the panel's own Close button out past its right edge onto the rail. | On a phone the panel is a sheet flush against the rail and the surface reserves only the rail's width; the tab row scrolls and Close keeps its place. | [before](before-phone-tool-panel.png) / [after](after-phone-tool-panel.png) |
| 5 | Artifacts head, both modes | Title + tabs + scope control on one non-wrapping row: **History was clipped off the screen edge**, and in Delegate the project filter with it. | The head wraps - title and scope on one line, the tab strip on its own scrolling line. | [before](before-phone-artifacts.png) / [after](after-phone-artifacts.png), Delegate [before](before-phone-delegate-artifacts.png) / [after](after-phone-delegate-artifacts.png) |
| 6 | Artifacts document viewer | Opening a markdown artifact made it a **viewport-wide fixed overlay** that covered the top bar and the tool rail - "← Gallery" was the only way back. The rule belonged to the Wiki destination; `WikiNote` is shared. | Scoped the full-screen sheet to `.wiki-main`; an artifact opens in the main window, as its own docs already said. | [before](before-phone-artifact-document.png) / [after](after-phone-artifact-document.png) |
| 7 | Master desk head (Delegate) | The backing-runner select truncated to `Claude Code (adapter does n…` in the two-up narrow grid. | At phone width that one control takes the full row; the short pills stay paired, so the head does not become six stacked rows. | [before](before-phone-delegate-desk.png) / [after](after-phone-delegate-desk.png) |
| 8 | Master popup | Inside the phone sheet, the desk's narrow-width thread bound (#152) applied, so the composer floated mid-panel above dead space. | The popup's thread fills its own sheet at every width. | [after](after-phone-master-popup.png) |
| 9 | Delegate mobile top bar | Menu used a different glyph (panel-left) than Work's hamburger for the same drawer. | One glyph, one affordance. | [after](after-phone-delegate-desk.png) |
| 10 | Composer | No safe-area allowance: Send sat against the home indicator. | `env(safe-area-inset-bottom)` on the phone composer. | [after](after-phone-work-chat.png) |

Dark, same geometry (tokens only, nothing hardcoded slipped in):
[before](before-phone-work-chat-dark.png) / [after](after-phone-work-chat-dark.png).

The #152 sub-900px single-document Master pattern is kept: the rail still stacks
under the conversation and the thread keeps its own bounded scrollport on the desk.

## 3. The floating Master trigger vs the composer

Chosen shape: **offset above the composer area, measured from the composer.**

The trigger reads every element marked `data-composer-dock` (the shared `Composer`,
and Work Chat's whole dock so its controls row counts too), keeps the ones anchored
to the viewport floor that share its horizontal band, and rises one gap above the
tallest. The clearance is published as `--master-popup-clearance`; CSS takes
`max(resting offset, clearance)`.

Measured after the change (`getBoundingClientRect`, expanded sidebar, Work Chat):

| Viewport | Trigger | Dock top | Overlaps dock | Overlaps Send |
| --- | --- | --- | --- | --- |
| 390 x 844 | bottom `649` | `661` | no | no |
| 1280 x 900 | bottom `708` | `720` | no | no |
| 1600 x 1000 | bottom `980` (resting corner) | `820`, right edge `1354` | no | no |

At 1600 the composer's centered column never reaches the trigger's band, so it stays
in the corner: the trigger only moves when it would actually cover something.

| Desktop 1280 before | Desktop 1280 after | Desktop 1600 after |
| --- | --- | --- |
| [before](before-desktop-1280-composer.png) | [after](after-desktop-1280-composer.png) | [after](after-desktop-1600-composer.png) |

Docking the trigger into the top bar was rejected: at 390px that row already carries
Menu, Back, the mode switch, Search, New chat, and the status cluster. Hiding it on
composer focus was rejected too - it leaves Send covered while the composer is merely
idle, which is how the owner found it.

## Known, not fixed here

In the **dark** theme the danger/warning/success surface tokens
(`--ui-danger-bg`, `--ui-success-bg`, `--ui-warning-bg` and their text partners) are
not overridden, so a Master error panel renders as a near-white card on the dark
surface - visible in [after](after-phone-delegate-desk.png)'s dark sibling. That is a
theme-preset gap, identical on desktop, and fixing it properly means auditing every
danger/warning/success surface in dark. It belongs in its own ticket rather than
riding along with a mobile-layout pass.

## Reproducing

1. `npm --prefix apps/web run build`
2. Start a disposable server (`apps/api/scripts/serve.py`) with `PROXIMA_DB_PATH`,
   `PROXIMA_HOME`, `PROXIMA_WORKSPACE_ROOT` and `PROXIMA_LINK_ROOTS` pointed at a
   scratch directory and `PROXIMA_WEB_DIST` at `apps/web/dist` - the environment
   `scripts/verify_master_browser.py` builds.
3. Set a password, link a couple of scratch folders as projects, seed a chat thread
   and a few Tasks, and mark the core tour done
   (`localStorage['proxima.tour.coreDone'] = '1'`, otherwise its modal blurs the
   shell).
4. Drive `?mode=work|delegate&view=…` and capture at `390x844` (2x) and
   `1280x900` / `1600x1000` (1x).
