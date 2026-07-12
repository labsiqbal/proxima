"""Session-kind registry — the contract for what a session *is*.

The main-chat gate must not hardcode knowledge of every feature's session type.
Instead each kind (identified by the ``sessions.mode`` value) is declared here with
its properties, and the core asks the registry — e.g. "which modes show in the main
chat list?" — rather than branching on string literals like ``mode == 'design'``.

Adding a new surface (a new session mode) becomes: register a ``SessionKind`` here.
The main-chat list, and any other registry-driven decision, follow automatically —
no edit to the chat gate.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionKind:
    mode: str                      # the sessions.mode value this kind is stored as
    shown_in_main_chat: bool       # does it appear in the main chat session list?
    feature_flag: str | None = None  # PROXIMA_FEATURE_* gate, if any


_REGISTRY: dict[str, SessionKind] = {}


def register(kind: SessionKind) -> None:
    _REGISTRY[kind.mode] = kind


def get(mode: str | None) -> SessionKind:
    """Resolve a session's kind; a NULL/unknown mode is treated as plain chat."""
    return _REGISTRY.get(mode or "chat", _REGISTRY["chat"])


def main_chat_modes() -> tuple[str, ...]:
    """The ``sessions.mode`` values that belong in the main chat list."""
    return tuple(k.mode for k in _REGISTRY.values() if k.shown_in_main_chat)


# --- built-in kinds ---------------------------------------------------------
# 'chat' is the main-chat gate itself. 'design' is Design Studio's session type,
# gated + excluded from the main list. New surfaces register alongside these.
register(SessionKind("chat", shown_in_main_chat=True))
register(SessionKind("design", shown_in_main_chat=False, feature_flag="design_studio"))
