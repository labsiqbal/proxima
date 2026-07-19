# ADR-0002: License — AGPL-3.0-or-later, pure commons (DCO, no CLA)

- Status: Accepted
- Date: 2026-07-14

## Context

Proxima is going open source: anyone can install and self-host it. The maintainer's explicit
goal is that it **cannot be captured** — nobody should be able to take Proxima, close the
source, and ship it as a proprietary product or a closed SaaS. It should grow as a commons
that stays open forever.

A clarification that shapes the choice: **no open-source license can forbid commercial use** —
that would violate the Open Source Definition (no field-of-use restrictions). So "must not
become a commercial product" cannot mean "no one may earn money"; it means **"no one may
*close* it."** The correct instrument for that is strong copyleft.

## Decision drivers

1. **Cannot be closed / captured** — derivatives, including hosted-service modifications, must
   stay open.
2. **Real open source** — OSI-approved, not merely "source available"; maximises legitimate
   adoption and contributor trust.
3. **Commons, not open-core** — the maintainer is not pursuing a dual-license business; every
   contributor, maintainers included, plays by the same rules.
4. **AI-and-human contributors** — the contribution mechanism must handle contributions that
   may be AI-generated.

## Options considered

- **MIT / permissive** — maximal adoption, **zero protection**: anyone may close the source
  and sell it. Directly contradicts driver 1. Rejected.
- **GPL-3.0** — strong copyleft, but leaves the **SaaS loophole**: a modified version run only
  as a network service (never distributed) need not share its changes. Proxima is a
  network-served control plane, so this loophole is exactly the gap that matters. Rejected.
- **Source-available, non-commercial** (BSL, PolyForm-NC, Commons Clause) — genuinely forbids
  commercial use, but is **not open source** (OSI). Loses adoption and contributor trust, and
  the goal is "can't be closed", not "no money exists". Rejected.
- **AGPL-3.0-or-later** — GPL-3 plus §13 (Remote Network Interaction): running a modified
  version over a network obliges you to offer users its source. Closes the SaaS loophole while
  remaining true, OSI-approved open source. Chosen.

Contribution governance: **DCO** (per-commit sign-off) vs **CLA** (rights assignment enabling
future relicensing / dual-licensing). A CLA is only needed to keep a commercial-relicensing
option; it adds friction and reduces community goodwill. Since Proxima is a pure commons with
no dual-license ambition, **DCO, no CLA.**

## Decision

License Proxima under **AGPL-3.0-or-later**. Governance is **pure commons**: contributors
certify each commit with the **Developer Certificate of Origin** (`git commit -s`); there is
**no CLA**. "or-later" is chosen so the project can adopt future FSF revisions, consistent with
its evolutionary posture.

AGPL permits commercial use (as any open-source license must) but forbids *closing* the
source — including for network/SaaS deployments — which is precisely the protection intended.

## Consequences

**Positive**

- The commons cannot be captured: any fork or hosted derivative must stay open, so the
  project can keep evolving with its community rather than being out-resourced by a closed
  competitor. This directly protects the "not meant to be done" premise.
- Real OSS status + DCO keeps the contribution barrier low (no CLA friction).

**Negative / accepted trade-offs**

- Some organisations bar AGPL software internally (e.g. Google) — a minor adoption limit,
  irrelevant to Proxima's self-hosting individual/small-team audience.
- No commercial-relicensing option is retained (the deliberate consequence of no CLA). If a
  dual-license business is ever wanted, that would require a new ADR **and** contributor
  agreement — it cannot be applied retroactively.

**Compliance follow-ups (tracked, not yet done):**

- `LICENSE` = canonical AGPL-3.0 text. ✅ (this PR)
- **AGPL §13 in-app source link** — because Proxima is a web app, the UI must offer network
  users a way to get the source (e.g. a "Source" link in About/footer). *Code change, follow-up.*
- `SPDX-License-Identifier: AGPL-3.0-or-later` headers on source files. *Follow-up sweep.*

## Related

- Supersedes: —
- Related: ADR-0001 (execution model); `CONTRIBUTING.md` (DCO + contribution flow).
