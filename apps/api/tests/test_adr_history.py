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
    assert "- Status: Accepted" in adr_0011
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
        "| Preview containment membership and detached output | Accepted |"
    ) in index
