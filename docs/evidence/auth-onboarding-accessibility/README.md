# Auth and onboarding accessibility evidence

This pass uses the production web bundle, a disposable owner database, and headless
Chrome at 1440 x 1000. The local flow does not read or alter live Proxima data.
The separate private-entry check sends unauthenticated GET requests only.

| Check | Result |
| --- | --- |
| First-run mismatch focus and single announcement | pass |
| Unsafe folder focus and single announcement | pass |
| Overlong display-name field routing | pass |
| Derived-slug collision field routing | pass |
| Pressed-button Tab and Space behavior | pass |
| Returning login failure and success | pass |
| Accessibility trees and one main landmark | pass |
| Every supported theme meets WCAG AA text contrast | pass |
| Lighthouse accessibility | 100 |
| Isolated Tailnet-host GET-only unauthenticated entry | pass |
| Private Tailscale unauthenticated entry | pass - private Tailscale origin (redacted) |

## Before and after

| Flow | Before | After |
| --- | --- | --- |
| Password gate | [tour capture](../../screenshots/first-run-password.png) | [setup mismatch](auth-setup-mismatch-after.png), [returning login](auth-login-error-after.png) |
| Folder onboarding | [legacy Link tab](../../screenshots/onboarding-link-folder.png), [legacy Create tab](../../screenshots/onboarding-create-folder.png) | [unsafe folder](onboarding-validation-after.png), [slug collision](onboarding-slug-collision-after.png) |
| Remote entry | - | [isolated Tailnet-host login](tailnet-unauthenticated-entry.png) |

Machine-readable details are in [report.json](report.json), with the full
[Lighthouse report](lighthouse.json). The private Tailscale origin is deliberately
redacted; only its label and passing state are retained.
