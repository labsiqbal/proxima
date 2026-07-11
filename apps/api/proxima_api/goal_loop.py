"""Autonomous goal-loop helpers.

The status sentinel the agent emits at the end of each goal turn, and the prompt
builder that frames a goal turn. Used by RunWorker (advancing the loop) and by
the chat routes (framing the first goal turn).
"""
from __future__ import annotations

import re

GOAL_INSTRUCTIONS = (
    "\n\n[Proxima goal mode] You are working autonomously toward a goal across multiple turns. "
    "Do as much real work as you can THIS turn (use tools and subagents); you'll be re-prompted "
    "automatically to continue, so make concrete progress rather than only planning. "
    "End EVERY reply with a status line as the final line — exactly one of:\n"
    "GOAL_STATUS: DONE  (the goal is fully achieved — stop)\n"
    "GOAL_STATUS: CONTINUE  (more work remains — you'll be re-prompted)\n"
    "GOAL_STATUS: BLOCKED  (you need the user's input to proceed — explain what above)\n"
    "If unsure, prefer CONTINUE while real work remains. Omitting the line is treated as DONE."
)
_GOAL_STATUS_RE = re.compile(r"GOAL_STATUS:\s*(DONE|CONTINUE|BLOCKED)", re.IGNORECASE)


def build_goal_prompt(objective: str, first: bool) -> str:
    if first:
        return f"Goal: {objective}{GOAL_INSTRUCTIONS}"
    return f"Continue toward the goal: {objective}{GOAL_INSTRUCTIONS}"


def parse_goal_status(text: str) -> str:
    """The agent's self-reported status for this turn. Absence ⇒ DONE (conservative:
    avoid burning iterations when the sentinel is missing). A question-form means the
    agent is waiting on the user, so that's BLOCKED regardless."""
    if "<question-form" in (text or ""):
        return "BLOCKED"
    found = _GOAL_STATUS_RE.findall(text or "")
    return found[-1].upper() if found else "DONE"
