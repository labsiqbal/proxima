from __future__ import annotations

import json
import re
from typing import Any

# Some runners (notably Pi) print a version banner + full skills catalog before
# the real answer. That noise poisons collab card previews and synthesis prompts.
# Real answers may start with ## Heading OR **bold** / plain prose (no second ##),
# so the dump must end on skill path bullets and --- separators, not only on ##.
_PI_PREAMBLE = re.compile(
    r"(?is)^\s*pi\s+v[\d.]+\b"
    r"(?:"
    r"\s*---"
    r"|\s*##\s+skills\b(?:[ \t]*-[ \t]+/\S+|\s)+"
    r")*"
    r"\s*(?:---\s*)?"
)
_SKILLS_HEADING = re.compile(
    r"(?is)^\s*##\s+skills\b(?:[ \t]*-[ \t]+/\S+|\s)*"
    r"(?:---\s*)?"
)
_UPDATE_NOTICE = re.compile(
    r"(?is)^\s*New version available:\s*\S+"
    r"(?:\s*\([^)]*\))?"
    r"(?:\.\s*Run:\s*`[^`]+`)?"
    r"\s*"
)


def strip_runner_preamble(text: str | None) -> str:
    """Drop leading runner banners/skills dumps; keep the real answer body."""
    if not text:
        return ""
    cleaned = text.strip()
    for _ in range(3):
        nxt = _PI_PREAMBLE.sub("", cleaned, count=1)
        nxt = _SKILLS_HEADING.sub("", nxt, count=1)
        nxt = _UPDATE_NOTICE.sub("", nxt, count=1)
        nxt = nxt.lstrip(" \t\r\n-")
        if nxt == cleaned:
            break
        cleaned = nxt
    return cleaned.strip()


def loads_list(raw: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def profile_label(profile: dict[str, Any]) -> str:
    runner = profile.get("runner_id") or "runner"
    return f"{profile.get('name') or 'Agent'} ({runner})"


DEBATE_ROUND_ROLES = ["stance", "rebuttal", "counter_rebuttal", "final_arguments"]
DEBATE_ROUND_LABELS = {
    "stance": "Round 1 · Opening stance",
    "rebuttal": "Round 2 · Rebuttal",
    "counter_rebuttal": "Round 3 · Counter-rebuttal",
    "final_arguments": "Round 4 · Final arguments",
    "synthesis": "Final · Judge/Synthesis",
}


def debate_round_role(round_number: int) -> str:
    if round_number < 1 or round_number > len(DEBATE_ROUND_ROLES):
        raise ValueError("debate round must be 1-4")
    return DEBATE_ROUND_ROLES[round_number - 1]


def collaboration_round_label(mode: str, role: str | None) -> str:
    role = role or "participant"
    if mode == "brainstorm":
        if role.startswith("idea:"):
            return f"Idea lane {role.split(':', 1)[1]}"
        if role == "synthesis":
            return "Synthesis"
        return "Idea lane"
    return DEBATE_ROUND_LABELS.get(role, "Debate round")


def collaboration_card_payload(
    collab: dict[str, Any],
    run_id: int,
    profile: dict[str, Any],
    role: str | None,
    status: str,
    text: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    mode = str(collab.get("mode") or "brainstorm")
    payload: dict[str, Any] = {
        "collaboration_id": collab.get("id"),
        "parent_run_id": collab.get("parent_run_id"),
        "run_id": run_id,
        "mode": mode,
        "agent_name": profile.get("name") or "Agent",
        "runner_id": profile.get("runner_id") or "runner",
        "role": role or "participant",
        "round_label": collaboration_round_label(mode, role),
        "status": status,
        "text": text,
    }
    if error:
        payload["error"] = error
    return payload


def build_brainstorm_child_prompt(user_prompt: str, profile: dict[str, Any], index: int) -> str:
    return (
        "You are one participant in a parallel Proxima Brainstorm. "
        "Work independently from the same user prompt. Do not review any previous answer.\n\n"
        "Your job: produce a distinct angle, not a generic complete answer.\n"
        "Return:\n"
        "1. Your core idea or approach.\n"
        "2. 3-5 concrete suggestions.\n"
        "3. Risks/tradeoffs.\n"
        "4. When your approach is best.\n\n"
        f"Participant: {profile_label(profile)}\n"
        f"Brainstorm lane: {index + 1}\n\n"
        f"User prompt:\n{user_prompt}"
    )


def build_brainstorm_synthesis_prompt(user_prompt: str, outputs: list[dict[str, Any]]) -> str:
    parts = "\n\n".join(
        f"## {o.get('profile_name')} ({o.get('runner_id')})\n{strip_runner_preamble(o.get('content', ''))}"
        for o in outputs
    )
    return (
        "You are the synthesis pass for a Proxima multi-agent Brainstorm. "
        "Read the independent agent drafts and produce one clear combined result.\n\n"
        "Return:\n"
        "1. Overlap/consensus.\n"
        "2. Unique useful angles from each agent.\n"
        "3. Recommended direction.\n"
        "4. Concrete next steps.\n\n"
        f"Original user prompt:\n{user_prompt}\n\n"
        f"Agent drafts:\n{parts}"
    )


def build_debate_stance_prompt(user_prompt: str, profile: dict[str, Any], side: str) -> str:
    return (
        "You are one side in a Proxima Debate. Take a clear position and argue it strongly. "
        "Do not hedge into synthesis yet.\n\n"
        "Return:\n"
        "1. Your position.\n"
        "2. The strongest arguments for it.\n"
        "3. What the opposing side will likely miss.\n\n"
        f"Debate side: {side}\n"
        f"Participant: {profile_label(profile)}\n\n"
        f"User prompt:\n{user_prompt}"
    )


def build_debate_rebuttal_prompt(user_prompt: str, prior: dict[str, Any], profile: dict[str, Any]) -> str:
    return (
        "You are the opposing side in a Proxima Debate. Read the first stance, then rebut it. "
        "Do not produce final synthesis yet.\n\n"
        "Return:\n"
        "1. Your opposing position.\n"
        "2. Rebuttal to the first stance.\n"
        "3. Strongest counterarguments.\n"
        "4. What your side concedes.\n\n"
        f"Participant: {profile_label(profile)}\n\n"
        f"Original user prompt:\n{user_prompt}\n\n"
        f"First stance from {prior.get('profile_name')} ({prior.get('runner_id')}):\n{strip_runner_preamble(prior.get('content', ''))}"
    )


def build_debate_followup_prompt(user_prompt: str, outputs: list[dict[str, Any]], profile: dict[str, Any], role: str) -> str:
    transcript = "\n\n".join(
        f"## {collaboration_round_label('debate', o.get('role'))} — {o.get('profile_name')} ({o.get('runner_id')})\n{strip_runner_preamble(o.get('content', ''))}"
        for o in outputs
    )
    if role == "counter_rebuttal":
        instruction = "Respond to the rebuttal with a counter-rebuttal. Strengthen the original side where possible, address concessions, and do not synthesize yet."
    elif role == "final_arguments":
        instruction = "Make final arguments after reading all prior rounds. Clarify remaining disagreement, strongest evidence, and concessions. Do not synthesize yet."
    else:
        instruction = "Continue the debate without producing final synthesis yet."
    return (
        "You are continuing a Proxima Debate. Read all previous rounds, then produce the next round only.\n\n"
        f"Round: {collaboration_round_label('debate', role)}\n"
        f"Participant: {profile_label(profile)}\n"
        f"Instruction: {instruction}\n\n"
        f"Original user prompt:\n{user_prompt}\n\n"
        f"Debate so far:\n{transcript}"
    )


def build_debate_synthesis_prompt(user_prompt: str, outputs: list[dict[str, Any]]) -> str:
    parts = "\n\n".join(
        f"## {o.get('role')} — {o.get('profile_name')} ({o.get('runner_id')})\n{strip_runner_preamble(o.get('content', ''))}"
        for o in outputs
    )
    return (
        "You are the neutral synthesis/judge for a Proxima Debate. "
        "Read the stance and rebuttal, then produce a recommendation.\n\n"
        "Return:\n"
        "1. Where the sides agree.\n"
        "2. Strongest point from each side.\n"
        "3. Decision/recommendation.\n"
        "4. Next action.\n\n"
        f"Original user prompt:\n{user_prompt}\n\n"
        f"Debate transcript:\n{parts}"
    )


def final_header(mode: str, user_prompt: str) -> str:
    # Shared by format_final and the worker's streamed header delta, so the
    # live bubble and the saved message stay byte-identical (no end snap).
    title = "⚖️ Debate result" if mode == "debate" else "🧠 Brainstorm result"
    return f"# {title}\n\n**Prompt:** {user_prompt.strip()}"


def format_final(mode: str, user_prompt: str, outputs: list[dict[str, Any]], synthesis: str) -> str:
    # Synthesis only — the per-agent detail already lives in the collaboration
    # cards above the message; repeating the transcript here just bloats the
    # thread.
    return f"{final_header(mode, user_prompt)}\n\n{synthesis.strip()}".strip()
