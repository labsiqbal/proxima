"""Owner-facing refusal wording: what was refused, why, and what to do next.

Proxima fails closed in a lot of places on purpose - the realpath jail, the
symlink policy, port ownership, Master runner conformance, script trust, push
remote pinning. The audit behind prune B5 (issue #115) found that roughly a
third of the owner's "errors everywhere with unknown causes" was not broken
behaviour at all: it was a *correct* refusal that never said what to do about
it. ``The listener lacks complete live managed-lineage proof`` is true, and
useless.

The rule this module enforces: **governance may refuse; it may never refuse
silently.** Every refusal that reaches the owner names three things -

1. **what** was refused (the caller supplies this),
2. **why** it was refused (the caller supplies this too - it knows the reason),
3. **the concrete next step** (this module owns it, keyed by refusal code).

Keeping (3) in one registry is what makes the rule testable: ``tests/
test_refusals.py`` asserts every entry is an imperative sentence, so a new
fail-closed state cannot ship with a dead-end message. It also keeps the
wording consistent across the two shapes the API already uses:

- ``refusal_message`` for the single-string convention (``FsError``,
  ``ContainerBoundaryError``, the preview status ``message`` field),
- ``refusal_detail`` for the structured convention
  (``HTTPException(detail={"code": ..., "message": ...})``), which additionally
  carries ``next_step`` on its own so a screen can style the instruction apart
  from the diagnosis.

Scope: **owner-facing surfaces only.** Refusals a runner sees (the Master tool
broker, the model-provider proxy) stay terse on purpose - telling a possibly
prompt-injected agent *how to get past* a boundary is the opposite of the goal
(see docs/prompt-injection-hardening.md). This module is never imported there.

Nothing here changes a decision to refuse. It changes only what the owner is
told about it.
"""

from __future__ import annotations

from typing import Any


#: Refusal code -> the concrete next step the owner should take.
#:
#: Each value is an instruction, not a restatement of the problem. When a step
#: names a control, it names the one the owner can actually see in the UI.
NEXT_STEPS: dict[str, str] = {
    # --- Preview / port authority (apprunner) --------------------------------
    "port_conflict": (
        "Stop whatever already holds that port, or press Change port to let "
        "Proxima pick a free one."
    ),
    "preview_ownership_unknown": (
        "Press Stop to release the port, then Run again; if it keeps "
        "happening, restart the Proxima server so no orphaned preview "
        "processes are left holding it."
    ),
    "app_output_sink_unavailable": (
        "Press Stop and then Run again; if the refusal repeats, restart the "
        "Proxima server so the preview supervisor is re-established."
    ),
    # --- Realpath jail and symlink policy (fsapi) ----------------------------
    "symlink_refused": (
        "Open the real folder this link points at instead, or replace the link "
        "with a real folder."
    ),
    "jail_escape": (
        "Pick a path inside this project; use a separate project for files "
        "that live outside its folder."
    ),
    "file_too_large": (
        "Open this file with an editor outside Proxima - it is past the size "
        "the in-app editor loads."
    ),
    "file_not_readable": (
        "Download the file instead - the in-app editor only opens text."
    ),
    # --- Container boundary (container_registry, layout_map) -----------------
    "container_root_symlink": (
        "Re-link this project to the real folder the link points at - Proxima "
        "pins a project to a real directory, never to a link."
    ),
    "container_root_gone": (
        "Use Relocate folder on the project card to point Proxima at where "
        "the folder is now, or unlink the project."
    ),
    "container_root_identity_changed": (
        "Use Relocate folder on the project card to re-pin the project to the "
        "folder as it exists now."
    ),
    "ops_root_symlink": (
        "Choose a real folder as this project's Ops folder in project "
        "settings - a link cannot be the jail anchor."
    ),
    "ops_root_missing": (
        "Pick an Ops folder that exists in project settings, or use Relocate "
        "folder if the whole project moved."
    ),
    "container_no_ops_area": (
        "Set this project's Ops folder in project settings so Proxima knows "
        "where its own files belong."
    ),
    # --- Master runner conformance -------------------------------------------
    "master_runner_not_conforming": (
        "Pick an eligible runner in the Master header's backing-runner menu, "
        "or install the version this one needs and reload."
    ),
    # --- Project lifecycle ----------------------------------------------------
    "purge_activity_blocked": (
        "Stop this project's running work - runs, previews, and terminals - "
        "then try again."
    ),
}


def next_step(code: str) -> str:
    """The registered next step for ``code``.

    Raises ``KeyError`` for an unregistered code on purpose: a refusal without
    a next step is the bug this module exists to prevent, so it fails loudly in
    tests rather than shipping an empty instruction.
    """
    return NEXT_STEPS[code]


def _sentence(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    return text if text[-1] in ".!?" else f"{text}."


def refusal_message(code: str, reason: str) -> str:
    """``reason`` (what + why) followed by the registered next step.

    For the single-string error convention: ``FsError``,
    ``ContainerBoundaryError``, and the preview status ``message`` field all
    flatten to one sentence-run the owner reads verbatim.
    """
    step = next_step(code)
    body = _sentence(reason)
    return f"{body} {step}" if body else step


def refusal_detail(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    """The structured refusal payload: ``code``, ``message``, ``next_step``.

    ``next_step`` is repeated as its own field so a screen can render the
    instruction apart from the diagnosis without re-parsing prose. ``extra``
    carries whatever the refusal's own flow needs (the runner id, the blocking
    processes, an override offer).
    """
    return {
        "code": code,
        "message": refusal_message(code, reason),
        "next_step": next_step(code),
        **extra,
    }
