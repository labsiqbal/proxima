# Auth and onboarding accessibility evidence

This pass uses the production web bundle, a disposable owner database, and headless
Chrome at 1440 x 1000. The local flow does not read or alter live Proxima data.
The command also runs focused API regressions for error ownership, readable-ancestor
selection, explicit no-ancestor failure, and the configured-root jail.
The private-entry browser check runs in an isolated profile, attaches one policy owner
to the page and every related worker target, accounts for every shell GET, and verifies
the current device Serve mapping. Page, dedicated-worker, and shared-worker traffic stays
intercepted through target closure. The production service worker is source-checked for
an inert static-only cache list, attached before it runs, and verified against that exact
request matrix. A development-served entry receives an inert no-socket Vite client
fixture, and any remaining outbound WebSocket handshake or frame fails the audit.

| Check | Result |
| --- | --- |
| First-run mismatch focus and single announcement | pass |
| Repeated mismatch gets one fresh announcement | pass |
| Unsafe folder focus and single announcement | pass |
| Repeated unsafe folder gets one fresh announcement with Enter | pass |
| Repeated unsafe folder gets one fresh announcement with Space | pass |
| Initial folder-load failure focuses an announced retry target | pass |
| Overlong display-name field routing | pass |
| Derived-slug collision field routing | pass |
| Missing create parent focuses selected-folder recovery | pass |
| Permission-denied create parent focuses selected-folder recovery | pass |
| Unreadable selection recovers to its nearest readable ancestor | pass |
| No readable ancestor retains explicit invalid state | pass |
| Browse recovery handles symlink cycles and remains inside configured roots | pass |
| Folder names respect the target filesystem component byte limit | pass |
| Missing selected folder focuses its refresh/reselect control | pass |
| Corrective targets and alerts have one semantic announcement owner | pass |
| Pressed-button Tab and Space behavior | pass |
| Returning login failure and success | pass |
| Accessibility trees and one main landmark | pass |
| Every gate text style in every supported theme meets WCAG AA contrast | pass |
| Input and button focus are visible in every supported theme | pass |
| Lighthouse accessibility | 100 |
| Production service-worker install and cache GET accounting | pass |
| Isolated Tailnet-host GET-only unauthenticated entry | pass |
| Remote browser accounts for page and worker shell GETs | pass |
| Remote browser blocks and accounts for WebSocket attempts | pass |
| Private Tailscale unauthenticated entry | pass - private Tailscale origin (redacted); current device Serve mapping verified (redacted) |

## Before and after

| Flow | Before | After |
| --- | --- | --- |
| Password gate | [tour capture](../../screenshots/first-run-password.png) | [setup mismatch](auth-setup-mismatch-after.png), [returning login](auth-login-error-after.png) |
| Folder onboarding | [legacy Link tab](../../screenshots/onboarding-link-folder.png), [legacy Create tab](../../screenshots/onboarding-create-folder.png) | [initial folder-load recovery](onboarding-folder-load-error-after.png), [unsafe folder](onboarding-validation-after.png), [slug collision](onboarding-slug-collision-after.png), [missing create parent](onboarding-create-parent-error-after.png), [permission-denied parent](onboarding-parent-permission-after.png), [missing selected folder](onboarding-path-error-after.png) |
| Remote entry | - | [isolated Tailnet-host login](tailnet-unauthenticated-entry.png) |

Machine-readable details are in [report.json](report.json), with the full
[Lighthouse report](lighthouse.json). The private Tailscale origin is deliberately
redacted; only its passing state and redacted current-device Serve provenance are retained.
