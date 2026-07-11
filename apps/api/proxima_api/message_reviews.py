"""Message-level Validate sidecar helpers."""
from __future__ import annotations

import json
import re
from typing import Any


_REVIEW_JSON_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        lines = [line.strip(" -\t") for line in value.splitlines()]
        return [line for line in lines if line]
    return [str(value)]


def _extract_json(text: str) -> dict[str, Any] | None:
    match = _REVIEW_JSON_RE.search(text or "")
    candidates: list[str] = []
    if match:
        candidates.append(match.group(1))
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            continue
    return None


def parse_review_output(text: str) -> dict[str, Any]:
    """Best-effort parse of a reviewer response.

    The prompt asks for JSON, but agent output is not guaranteed. Store raw text and
    extract known fields when possible so the UI still has a useful fallback.
    """
    parsed = _extract_json(text) or {}
    verdict = str(parsed.get("verdict") or "unclear").strip() or "unclear"
    revised = str(parsed.get("revised_content") or parsed.get("revised") or "").strip()
    suggested = str(parsed.get("suggested_next_move") or parsed.get("next_move") or "").strip()
    gaps = _as_list(parsed.get("gaps") or parsed.get("missing") or parsed.get("risks"))
    depends = _as_list(parsed.get("depends_on_input") or parsed.get("depends_on_unanswered_input"))
    if not parsed:
        revised = text.strip()
    return {
        "verdict": verdict[:120],
        "gaps": gaps,
        "depends_on_input": depends,
        "revised_content": revised,
        "suggested_next_move": suggested,
        "raw_transcript": text,
    }


def review_payload(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("reviewer_profiles", "gaps", "depends_on_input"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except Exception:
            data[key] = []
    return data


def build_source_merge_prompt(
    *,
    source_content: str,
    validation_feedback: str,
    reviewer_revision: str | None = None,
    note: str | None = None,
) -> str:
    extra = f"\n\nUser note: {note.strip()}" if note and note.strip() else ""
    revised = f"\n\nReviewer's revised candidate:\n{reviewer_revision}" if reviewer_revision else ""
    return f"""You are the original/source agent. A reviewer validated your previous answer.

Revise your previous answer by merging the useful validation feedback. Preserve the original intent, address concrete gaps, and return ONLY the full improved answer. Do not wrap it in JSON and do not explain the review process.{extra}

Original answer:
---
{source_content}
---

Validation feedback:
---
{validation_feedback}
---{revised}
""".strip()


def build_validate_prompt(
    *,
    source_content: str,
    source_author: str | None,
    source_runner: str | None,
    session_title: str,
    has_unanswered_qform: bool,
) -> str:
    qform_note = ""
    if has_unanswered_qform:
        qform_note = (
            "\n\nImportant: the source response contains an unanswered <question-form>. "
            "Validate the response snapshot as-is. Do not choose an answer on the user's behalf. "
            "If your critique or revision depends on that missing user input, put it in depends_on_input."
        )
    return f"""You are validating another AI agent's answer for the user.

Session title: {session_title}
Source agent: {source_author or 'unknown'} ({source_runner or 'unknown runner'})

Your job:
- Find missing assumptions, risks, edge cases, and weak spots.
- Preserve what is already good.
- Produce a revised version that improves the answer without mutating the original chat.
- Be direct but constructive; this is a validation gate, not a roast.{qform_note}

Source response to validate:

---
{source_content}
---

Return your final answer as JSON in a fenced ```json block with exactly these keys:
{{
  "verdict": "solid|needs_work|risky|unclear",
  "gaps": ["specific missing/risky point"],
  "depends_on_input": ["only if unanswered user input matters"],
  "revised_content": "the improved answer/proposal",
  "suggested_next_move": "continue|use_revised|ask_original_to_revise|brainstorm|debate"
}}

You may include a short human-readable explanation before the JSON, but the JSON block is required.
""".strip()
