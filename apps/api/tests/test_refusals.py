"""Governance may refuse; it may never refuse silently (prune B5, #133).

These tests pin the *shape* of every owner-facing refusal: what was refused,
why, and the concrete next step. The registry is the single place that wording
lives, so a new fail-closed state cannot ship with a dead-end message.
"""

from __future__ import annotations

import re

import pytest

from proxima_api import refusals


IMPERATIVE = re.compile(r"^[A-Z][a-z]+")


def test_registry_is_not_empty():
    assert refusals.NEXT_STEPS


@pytest.mark.parametrize("code", sorted(refusals.NEXT_STEPS))
def test_every_next_step_is_a_concrete_instruction(code: str):
    step = refusals.NEXT_STEPS[code]
    # It must read as an instruction to the owner, not a restatement of the
    # refusal: a capitalised imperative sentence that ends in a full stop.
    assert IMPERATIVE.match(step), f"{code}: next step must start with a verb"
    assert step.endswith("."), f"{code}: next step must be a sentence"
    assert len(step) > 20, f"{code}: next step is too vague to act on"
    lowered = step.lower()
    assert not lowered.startswith(("proxima ", "the ", "this ", "it ")), (
        f"{code}: next step describes state instead of naming an action"
    )


def test_next_step_rejects_an_unregistered_code():
    with pytest.raises(KeyError):
        refusals.next_step("not_a_refusal")


def test_refusal_message_appends_the_next_step():
    message = refusals.refusal_message(
        "symlink_refused", "That path crosses a symlink, which Proxima never follows."
    )
    assert message.startswith("That path crosses a symlink")
    assert message.endswith(refusals.NEXT_STEPS["symlink_refused"])


def test_refusal_message_normalises_a_reason_without_a_full_stop():
    message = refusals.refusal_message("symlink_refused", "path crosses a symlink")
    assert "symlink. " in message
    assert message.endswith(refusals.NEXT_STEPS["symlink_refused"])


def test_refusal_detail_carries_code_message_and_next_step():
    detail = refusals.refusal_detail(
        "master_runner_not_conforming",
        "Codex cannot run Master because its adapter is unproven.",
        runner_id="codex",
    )
    assert detail["code"] == "master_runner_not_conforming"
    assert detail["next_step"] == refusals.NEXT_STEPS["master_runner_not_conforming"]
    assert detail["message"].endswith(detail["next_step"])
    assert detail["runner_id"] == "codex"


def test_the_documented_refusal_inventory_is_covered():
    """The B5 inventory. Removing a code here without removing the refusal it
    names would silently re-open a dead-end message."""
    assert {
        "app_output_sink_unavailable",
        "container_root_gone",
        "container_root_identity_changed",
        "container_root_symlink",
        "container_no_ops_area",
        "file_not_readable",
        "file_too_large",
        "jail_escape",
        "master_runner_not_conforming",
        "ops_root_symlink",
        "ops_root_missing",
        "port_conflict",
        "preview_ownership_unknown",
        "purge_activity_blocked",
        "symlink_refused",
    } <= set(refusals.NEXT_STEPS)
