# Delegate Master desk: chat surface and rail evidence

Real-browser before/after for #152, captured against a **disposable** instance: its
own database, own `HOME`, own workspace under `/tmp`, and a fake runner. The live
owner instance and every linked real folder were untouched.

| View | Before | After |
| --- | --- | --- |
| Desk at 1600px | [floating composer, unshared column](before-desk-1600.png) | [anchored chat column](after-desk-1600.png) |
| Desk at 1600px, Dark | - | [same geometry, tokens only](after-desk-1600-dark.png) |
| Desk at 820px | - | [two-up header, thread, composer](after-desk-narrow.png) |
| Desk at 820px, scrolled | - | [rail stacked under the conversation](after-desk-narrow-rail.png) |

What the before shot shows: the composer hangs mid-canvas at a different width
from the history, the target picker sits at the far left, the five stat pills are
boxed tiles, and each rail card spends two lines on its empty state.

## Reproducing

There is no one-command capture for this pass. The recipe:

1. `npm --prefix apps/web run build`
2. Start a disposable server (`apps/api/scripts/serve.py`) with `PROXIMA_DB_PATH`,
   `PROXIMA_HOME`, `PROXIMA_WORKSPACE_ROOT`, and `PROXIMA_LINK_ROOTS` pointed at a
   scratch directory and `PROXIMA_WEB_DIST` at `apps/web/dist` - the same
   environment `scripts/verify_master_browser.py` builds.
3. Set a password, link two scratch projects, then seed the Master session with a
   few messages, Tasks across all five desk states, one decision, one attention
   item, and three checkpoints.
4. Open `?mode=delegate&view=master` at 1600x1000 and 820x1000, in the Sunset and
   Dark themes, and capture.
