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
            "Exact containment proof gates preview authority"
        ),
        "0013-detached-preview-output-uses-os-sink-helpers.md": (
            "Detached preview output uses OS sink helpers"
        ),
        "0014-automatic-preview-relay-binds-explicit-interfaces.md": (
            "Automatic preview relay binds explicit interfaces"
        ),
        "0015-preview-authentication-precedes-target-resolution.md": (
            "Preview authentication precedes target resolution"
        ),
    }
    for filename, title in successors.items():
        adr = (ROOT / "docs" / "adr" / filename).read_text(
            encoding="utf-8",
        )
        assert "- Status: Accepted" in adr
        assert adr.count("\n## Decision\n") == 1
        assert f"| {title} | Accepted |" in index
