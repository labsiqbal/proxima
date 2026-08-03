# Collapsed left sidebar rail: before/after evidence

Real-browser captures for #153, taken against a **disposable** instance: its own
database, own `HOME`, own workspace and own link root under `/tmp`. The live
owner instance and every linked real folder were untouched; the fixture was
removed after the run.

All shots are the top-left corner of the shell at 1440x960, 2x device pixels.

| View | Before | After |
| --- | --- | --- |
| Collapsed rail, Sunset (default theme) | [wrapped eyebrow, "A" pill, oversized active tile](before-rail-collapsed-sunset.png) | [one tile column](after-rail-collapsed-sunset.png) |
| Collapsed rail, Dark | [same, dark](before-rail-collapsed-dark.png) | [same geometry, tokens only](after-rail-collapsed-dark.png) |
| Collapsed rail, Light | - | [same geometry, tokens only](after-rail-collapsed-light.png) |
| Expanded sidebar, Sunset | pixel-identical | [unchanged](after-rail-expanded-sunset.png) |
| Expanded sidebar, Dark | pixel-identical | [unchanged](after-rail-expanded-dark.png) |

What the before shows: "WORK PROJECT" wraps onto two lines and overflows the
52px rail, the switcher is a text pill truncated to one letter plus a chevron,
and the active destination's tile is a wide rounded rectangle that no inactive
icon shares.

Supporting after-shots:

- [hover](after-rail-hover-sunset.png) - the hovered tile takes exactly the
  active tile's footprint, and the destination label returns as the tooltip
  beside it.
- [popover](after-rail-popover-sunset.png) - the project tile still opens the
  full switcher, every project by name and slug.
- [update pill](after-rail-update-sunset.png) - the update affordance becomes
  another tile instead of wrapping. The pill needs a pending release to render,
  so this one shot has the element injected into the page: a check of the
  stylesheet's geometry, not of update behaviour.

Measured after the change (`getBoundingClientRect`, default 14px root):
rail `52px` wide; project tile and all five destination tiles `36x36` at `x: 8`;
tile tops `64, 108, 152, 196, 240, 284` - one 8px gutter everywhere, horizontally
and vertically.

## Reproducing

1. `npm --prefix apps/web run build`
2. Start a disposable server (`apps/api/scripts/serve.py`) with `PROXIMA_DB_PATH`,
   `PROXIMA_HOME`, `PROXIMA_WORKSPACE_ROOT` and `PROXIMA_LINK_ROOTS` pointed at a
   scratch directory and `PROXIMA_WEB_DIST` at `apps/web/dist` - the environment
   `scripts/verify_master_browser.py` builds.
3. Set a password, link a couple of scratch folders as projects, mark the core
   tour done (`localStorage['proxima.tour.coreDone'] = '1'`, otherwise its modal
   blurs the shell), and set `proxima.leftCollapsed` / `proxima.theme`.
4. Capture the corner at `1440x960` with a `deviceScaleFactor` of 2.
