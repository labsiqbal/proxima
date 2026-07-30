# Auth and onboarding accessibility evidence

This pass uses the production web bundle, a disposable owner database, and headless
Chrome at 1440 x 1000. It does not read or alter live Proxima data.

| Check | Result |
| --- | --- |
| First-run mismatch focus and single announcement | pass |
| Unsafe folder focus and single announcement | pass |
| Overlong display-name field routing | pass |
| Pressed-button Tab and Space behavior | pass |
| Returning login failure and success | pass |
| Accessibility trees and one main landmark | pass |
| Six-theme WCAG AA text contrast | pass |
| Lighthouse accessibility | 100 |
| Isolated Tailnet-host unauthenticated entry | pass |
| Private Tailscale unauthenticated entry | not configured - set PROXIMA_A11Y_REMOTE_BASE to retain a private Tailnet pass |

## Before and after

| Flow | Before | After |
| --- | --- | --- |
| Password gate | [tour capture](../../screenshots/first-run-password.png) | [setup mismatch](auth-setup-mismatch-after.png), [returning login](auth-login-error-after.png) |
| Folder onboarding | [link](../../screenshots/onboarding-link-folder.png), [create](../../screenshots/onboarding-create-folder.png) | [unsafe folder](onboarding-validation-after.png) |
| Remote entry | - | [isolated Tailnet-host login](tailnet-unauthenticated-entry.png) |

Machine-readable details are in [report.json](report.json), with the full
[Lighthouse report](lighthouse.json).
