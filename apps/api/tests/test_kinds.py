"""Unit tests for the session-kind registry (proxima_api.kinds)."""
from __future__ import annotations

from proxima_api import kinds


def test_chat_is_shown_in_main_chat():
    assert "chat" in kinds.main_chat_modes()


def test_design_is_registered_but_hidden_from_main_chat():
    assert kinds.get("design").mode == "design"
    assert kinds.get("design").shown_in_main_chat is False
    assert "design" not in kinds.main_chat_modes()


def test_unknown_or_null_mode_resolves_to_chat():
    assert kinds.get(None).mode == "chat"
    assert kinds.get("totally-new-thing").mode == "chat"


def test_registering_a_hidden_kind_does_not_leak_into_main_chat():
    before = set(kinds.main_chat_modes())
    kinds.register(kinds.SessionKind("scratch-surface", shown_in_main_chat=False))
    try:
        assert set(kinds.main_chat_modes()) == before  # a hidden new surface never touches the gate
    finally:
        kinds._REGISTRY.pop("scratch-surface", None)
