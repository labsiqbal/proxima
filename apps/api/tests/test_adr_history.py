from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_preview_authority_adr_history_is_append_only():
    adr_0010 = (
        ROOT
        / "docs"
        / "adr"
        / "0010-preview-authority-requires-verified-connections.md"
    ).read_text(encoding="utf-8")
    assert "- Status: Superseded by ADR-0011" in adr_0010
    accepted_0010 = adr_0010.replace(
        "- Status: Superseded by ADR-0011",
        "- Status: Accepted",
    )
    assert hashlib.sha256(accepted_0010.encode()).hexdigest() == (
        "8161bbaeffd102387749dc7a144f96478c4df166c89b46bcfb5680a193e018ee"
    )

    adr_0011 = (
        ROOT
        / "docs"
        / "adr"
        / "0011-preview-containment-membership-and-detached-output.md"
    ).read_text(encoding="utf-8")
    assert "- Status: Superseded by ADR-0012" in adr_0011
    accepted_0011 = adr_0011.replace(
        "- Status: Superseded by ADR-0012",
        "- Status: Accepted",
    )
    assert hashlib.sha256(accepted_0011.encode()).hexdigest() == (
        "23881eb7a47ee1aae99d7235f239d615739608df7f964589e1dbc9c4650e5133"
    )
    assert "Supersedes ADR-0010" in adr_0011

    index = (ROOT / "docs" / "adr" / "README.md").read_text(
        encoding="utf-8",
    )
    assert (
        "0010-preview-authority-requires-verified-connections.md) "
        "| Preview authority requires verified managed connections "
        "| Superseded by ADR-0011 |"
    ) in index
    assert (
        "0011-preview-containment-membership-and-detached-output.md) "
        "| Preview containment membership and detached output "
        "| Superseded by ADR-0012 |"
    ) in index

    successors = {
        "0012-exact-containment-proof-gates-preview-authority.md": (
            "Exact containment proof gates preview authority",
            "Superseded by ADR-0016",
        ),
        "0013-detached-preview-output-uses-os-sink-helpers.md": (
            "Detached preview output uses OS sink helpers",
            "Superseded by ADR-0018",
        ),
        "0014-automatic-preview-relay-binds-explicit-interfaces.md": (
            "Automatic preview relay binds explicit interfaces",
            "Accepted",
        ),
        "0015-preview-authentication-precedes-target-resolution.md": (
            "Preview authentication precedes target resolution",
            "Accepted",
        ),
        "0016-live-containment-lineage-gates-preview-authority.md": (
            "Live containment lineage gates preview authority",
            "Accepted",
        ),
        "0017-manager-owned-provisional-preview-cleanup.md": (
            "Manager-owned provisional preview cleanup",
            "Superseded by ADR-0020",
        ),
        "0018-preview-status-log-framing-is-bounded.md": (
            "Preview status log framing is bounded",
            "Accepted",
        ),
        "0019-launch-time-broker-owns-preview-output.md": (
            "Launch-time broker owns preview output",
            "Superseded by ADR-0021",
        ),
        "0020-preview-lifecycles-use-project-generations.md": (
            "Preview lifecycles use project generations",
            "Accepted",
        ),
        "0021-preview-supervisors-own-app-scopes.md": (
            "Preview supervisors own app scopes",
            "Accepted",
        ),
        "0022-preview-log-polling-uses-versioned-deltas.md": (
            "Preview log polling uses versioned deltas",
            "Accepted",
        ),
        "0023-preview-supervisor-profiles-are-isolated.md": (
            "Preview supervisor profiles are isolated",
            "Accepted",
        ),
    }
    for filename, (title, status) in successors.items():
        adr = (ROOT / "docs" / "adr" / filename).read_text(
            encoding="utf-8",
        )
        assert f"- Status: {status}" in adr
        assert adr.count("\n## Decision\n") == 1
        assert f"| {title} | {status} |" in index

    adr_0012 = (
        ROOT
        / "docs"
        / "adr"
        / "0012-exact-containment-proof-gates-preview-authority.md"
    ).read_text(encoding="utf-8")
    accepted_0012 = adr_0012.replace(
        "- Status: Superseded by ADR-0016",
        "- Status: Accepted",
    )
    assert hashlib.sha256(accepted_0012.encode()).hexdigest() == (
        "42d2c0bf1c60918292847edfe0d6a83696ee7ffefc0436ec050fbc5ce6b25b0d"
    )

    adr_0013 = (
        ROOT
        / "docs"
        / "adr"
        / "0013-detached-preview-output-uses-os-sink-helpers.md"
    ).read_text(encoding="utf-8")
    accepted_0013 = adr_0013.replace(
        "- Status: Superseded by ADR-0018",
        "- Status: Accepted",
    )
    assert hashlib.sha256(accepted_0013.encode()).hexdigest() == (
        "2109b66d492b89b0fe822c4948a0589d2d9111eeb1e86ab87d8e6e740a1f46f0"
    )
