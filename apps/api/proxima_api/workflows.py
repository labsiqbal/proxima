"""Pure helpers for the Workflows/Jobs feature: step normalization, per-step
prompt building, and chat→workflow blueprint parsing. No DB or HTTP here so this
stays unit-testable and reusable from both the request handlers and the worker."""

from __future__ import annotations

import json as _json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from .graph import normalize_graph


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


# Reserved step fields land here with safe defaults. `rules`/`skill_ids` (M2),
# `review_required` (M2), and `depends_on` (M3) are carried but dormant in M1.
STEP_DEFAULTS: dict[str, Any] = {
    "expected_output": "",
    "type": "other",
    "rules": None,
    "skill_ids": None,
    "review_required": False,
    "depends_on": None,
}


def normalize_step(raw: dict[str, Any]) -> dict[str, Any]:
    step = {
        "id": (raw.get("id") or uuid.uuid4().hex[:8]),
        "name": (raw.get("name") or "Step").strip(),
        "instruction": (raw.get("instruction") or "").strip(),
    }
    for key, default in STEP_DEFAULTS.items():
        step[key] = raw.get(key, default)
    return step


def normalize_steps(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [normalize_step(s) for s in (raw or [])]


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def substitute(text: str, inputs: dict[str, Any] | None) -> str:
    """Replace {{key}} placeholders with run input values. Unknown keys are left
    as-is so a missing input is visible rather than silently blanked."""
    if not text or not inputs:
        return text or ""
    return _VAR_RE.sub(lambda m: str(inputs.get(m.group(1), m.group(0))), text)


def build_step_prompt(step: dict[str, Any], idx: int, total: int, inputs: dict[str, Any] | None = None) -> str:
    """The prompt handed to the agent for one workflow step. Prior steps' outputs
    are already in the persistent ACP session history, so we only inject this
    step's instruction + contract. {{var}} placeholders are filled from run inputs."""
    instruction = substitute(step["instruction"], inputs)
    expected = substitute(step.get("expected_output") or "A clear, complete result for this step.", inputs)
    rules = substitute((step.get("rules") or "").strip(), inputs)
    rules_block = f"\nRULES (must follow exactly):\n{rules}\n" if rules else ""
    skill_ids = step.get("skill_ids") or []
    skills_block = f"\nSUGGESTED SKILLS/TOOLS for this step: {', '.join(skill_ids)}.\n" if skill_ids else ""
    return (
        "⟦MODE: AUTONOMOUS RUN⟧ Execute fully and silently. Decide on your own (note assumptions). "
        "NEVER ask the user or emit a <question-form>; refinement happens later in the iterate sandbox.\n\n"
        f"You are executing workflow step {idx + 1} of {total}: {step['name']}.\n\n"
        f"INSTRUCTION:\n{instruction}\n\n"
        f"EXPECTED OUTPUT:\n{expected}\n"
        f"{rules_block}{skills_block}\n"
        "Do only this step; build on the prior steps already in this conversation; "
        "produce the expected output. This is an AUTONOMOUS run — do NOT ask the user "
        "or emit a <question-form>; make sensible, on-brand decisions and proceed, noting "
        "any assumptions. If you truly cannot proceed (missing access, credentials, or "
        "information), reply starting with 'BLOCKED:' and explain why."
    )


def build_continuation_prompt(continuation: int, limit: int) -> str:
    """The prompt for a timeout auto-continuation turn (T5): a genuine resume in
    the SAME agent session (full context) and, for repo jobs, the SAME worktree
    (file edits persist) - never a re-brief or restart-from-scratch."""
    return (
        "⟦MODE: CONTINUATION⟧ Your previous turn on this job hit the per-turn time "
        f"limit and was cut off mid-work (automatic continuation {continuation} of {limit}). "
        "Your working directory and this conversation are unchanged: everything you already "
        "did is still there. Inspect the current state of your work - the files you already "
        "changed, the output you already produced, your original instruction earlier in this "
        "conversation - and CONTINUE from where it stopped. Do not start over and do not redo "
        "finished work. Complete the remaining part of the original instruction and produce "
        "the expected output it asked for."
    )


_DESIGN_CAPABILITY_PREAMBLE = """# Proxima capabilities (available to you in this Proxima project)

Your working directory IS the project root. Decide from THIS step's instruction which of the
project's native features fit — use them when relevant, otherwise just produce the step's expected
output as text.

**Design Studio** — to produce an EDITABLE visual design (social post, poster, slide deck, mobile
screen, …), write a scene file to `artifacts/design/<id>/scene.json` (one folder per design;
`<id>` is a short slug that MUST equal the folder name). It then appears automatically in Design
Studio, openable and editable. Keep text as REAL text layers — NEVER bake text into an image.
Act like an art director: if this is an interactive iterate/chat turn and the brief is generic,
ask a compact <question-form> for only the missing high-impact creative decisions (goal, audience,
copy, visual lead, mood/style, generated image needed, layout direction, CTA, brand/design-system
constraints). Do NOT use a fixed form; choose questions and answer options for the specific brief.
If the brief is already clear or this is an autonomous workflow/job run, make sensible art-direction
decisions and proceed.
Schema:
{ "id": "<id>", "type": "graphic|deck|mobile", "title": "...",
  "artboards": [ { "id": "a1", "width": 1080, "height": 1080, "background": "#ffffff",
    "layers": [
      { "type":"text", "x":0,"y":0,"width":860,"text":"...","fontSize":78,"fontFamily":"Playfair Display","fontStyle":"bold","fill":"#0A0A0A","align":"center","lineHeight":1.05,"shadow":true,"shadowBlur":14,"shadowOpacity":0.25 },
      { "type":"rect", "x":0,"y":0,"width":360,"height":84,"fill":"#FF6A00","cornerRadius":42,"shadow":true },
      { "type":"ellipse|triangle|star", "x":0,"y":0,"width":120,"height":120,"fill":"#fff","shadow":true },
      { "type":"line", "x":0,"y":0,"x2":100,"y2":0,"stroke":"#000","strokeWidth":2 },
      { "type":"path", "x":0,"y":0,"width":300,"height":300,"d":"<SVG path>","fill":"#000" },
      { "type":"image", "x":0,"y":0,"width":1080,"height":1080,"src":"gen:<short prompt>","cornerRadius":0 }
    ] } ] }
Every layer needs a unique `id`, plus `x`,`y` (and optional `rotation` deg, `opacity` 0..1). For a
photo/illustration, set an image layer's `src` to `"gen:<short prompt>"` (Proxima generates it). Common
sizes: IG post 1080×1080, story/reel 1080×1920, poster 1080×1350, deck 1920×1080, mobile 390×844.
Fonts: Inter, Poppins, Nunito, Merriweather, Playfair Display, Roboto Slab, JetBrains Mono, Oswald, Caveat, Lobster.
Avoid defaulting to flat text + button + basic shapes. Build a complete composition with a visual
focal point, hierarchy, spacing, accents, and depth. Use editable effects where useful: text
shadow/glow, shape shadow, opacity, translucent panels, fine strokes, overlapping layers, generated
hero images, and organic path/blob shapes. Choose styles intentionally: glassmorphism, Apple-like
clean, premium editorial, neon/cyber, playful creator, clean SaaS, etc. Do not force an eyebrow,
button, or CTA when the concept does not need one.
If the step/request implies a visual subject (product, food, person, venue, event, mood scene,
illustration, campaign visual, hero object, etc.), include at least one image layer with
`src:"gen:<specific visual prompt>"` by default unless the user explicitly wants type-only or no
image. Use it as a hero, background, product shot, illustration, or texture so the design is not
only fonts and shapes.

**Project files** — read/refer to existing files; save outputs in the project so they show in the
Files tab. **Wiki / memory** — the project wiki context is provided above; consult it (durable
knowledge is logged automatically).
"""


_BASE_CAPABILITY_PREAMBLE = """# Proxima capabilities (available to you in this Proxima project)

Your working directory IS the project root. Use the project's native features when relevant;
otherwise produce the step's expected output as text.

**Project files** — read existing files and save deliverables in the project so they show in the
Files tab. **Wiki / memory** — consult the project wiki context provided above.
"""

def build_capability_preamble(*, include_design_studio: bool = False) -> str:
    return _DESIGN_CAPABILITY_PREAMBLE if include_design_studio else _BASE_CAPABILITY_PREAMBLE


def build_iteration_preamble(name: str, steps: list[dict[str, Any]], *, include_design_studio: bool = False) -> str:
    """Seed for a workflow's iterate/test chat: the current recipe + a sandbox brief.
    The capability preamble is appended separately so dry-tests produce real output."""
    lines = []
    for i, s in enumerate(steps):
        bits = [f"{i + 1}. {s.get('name') or 'Step'}: {s.get('instruction') or ''}".strip()]
        if s.get("expected_output"):
            bits.append(f"   → expected: {s['expected_output']}")
        if s.get("rules"):
            bits.append(f"   → rules: {s['rules']}")
        lines.append("\n".join(bits))
    recipe = "\n".join(lines) if lines else "(no steps yet)"
    return (
        "⟦MODE: INTERACTIVE SANDBOX⟧ You MAY ask the user clarifying questions and suggest "
        "improvements. This is a dry-test for refining the recipe, NOT an official run.\n\n"
        f"# Workflow iteration sandbox — \"{name}\"\n\n"
        "You are helping the user TEST and REFINE a reusable workflow (a recipe of steps) before "
        "they finalize it.\n\n"
        f"CURRENT RECIPE:\n{recipe}\n\n"
        "How to help:\n"
        "- When the user asks, EXECUTE a step (or the whole recipe) and show the REAL output (you can "
        + ("produce designs/files" if include_design_studio else "produce project files")
        + " — see capabilities below), so they can judge it.\n"
        "- Suggest concrete improvements to a step's instruction / expected output / rules.\n"
        "- Keep your replies focused on building the recipe. When the user is happy, they'll click "
        "\"Save to workflow\" to fold this conversation back into the recipe.\n"
    )


def build_recipe_context(name: str, steps: list[dict[str, Any]]) -> str:
    """A compact 'current recipe' block re-injected on each iterate turn so the agent
    always reflects the latest steps (incl. edits made directly in the stage editor)."""
    lines = []
    for i, s in enumerate(steps):
        line = f"{i + 1}. {s.get('name') or 'Step'}: {s.get('instruction') or ''}".strip()
        if s.get("rules"):
            line += f" [rules: {s['rules']}]"
        lines.append(line)
    body = "\n".join(lines) if lines else "(no steps yet)"
    return f"‹CURRENT RECIPE for \"{name}\" (live — may have been edited since we last spoke):\n{body}›"


def step_state_from(step: dict[str, Any], inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Snapshot a recipe step into a job's per-step execution record."""
    state = {
        **step,
        "status": "queued",
        "run_id": None,
        "output_summary": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
    }
    if inputs:
        for key in ("instruction", "expected_output", "rules"):
            if isinstance(state.get(key), str):
                state[key] = substitute(state[key], inputs)
    return state


# --- chat → workflow promotion ---

ARCHITECT_SYSTEM = (
    "You convert a conversation into a reusable WORKFLOW (a repeatable recipe). "
    "Extract the repeatable PROCESS discussed, not just the last message. "
    "Respond with ONLY a JSON object, no prose, of the form: "
    '{"name": str, "description": str, "category": str, '
    '"steps": [{"name": str, "instruction": str, "expected_output": str, "type": str}]}. '
    "Each step is one agent action with a clear instruction and the output it should produce. "
    "Order the steps so each builds on the previous. Keep step instructions self-contained. "
    "category is one of: content, seo, build, audit, research, other."
)

GRAPH_ARCHITECT_SYSTEM = (
    "You are a work planner. Slice the conversation's goal into a runnable plan: "
    "a DAG of jobs. Extract the repeatable process, not merely the last message. "
    "Respond with ONLY one JSON object and no prose: "
    '{"name": str, "description": str, "category": str, "graph": {'
    '"nodes": [{"id": str, "name": str, "instruction": str, '
    '"expected_output": str, "output_kind": "text|json|artifact-ref", '
    '"output_schema": object|null, "review_required": bool, '
    '"target": str|null, "target_ambiguous": bool, "target_question": str|null, '
    '"depends_on": [node_id]}], "edges": [{"from": node_id, "to": node_id}]}}. '
    "Use stable short kebab-case node ids. Express every dependency; independent nodes "
    "may share the same dependencies. Keep each instruction self-contained because every "
    "node runs in a fresh agent session with only explicit job input and upstream outputs. "
    "Use output_kind=json only when downstream nodes need structured data, with a valid "
    "JSON Schema when useful. Use artifact-ref only for concrete files/directories created "
    "inside the project. Set review_required only at meaningful human gates. The graph must "
    "be acyclic. category is one of content, seo, build, audit, research, other.\n"
    "Every job binds to exactly ONE work area, chosen NOW, not at runtime: set target to "
    "one of the project's code areas (listed below) when the job edits that repo's files, "
    "or to \"ops\" for everything else (research, writing, review, reports, designs). "
    "If it is genuinely unclear which area a job should work in, do NOT guess: set "
    "target to null, target_ambiguous to true, and put the question for the owner in "
    "target_question — the plan will surface it before running. All repo jobs of one "
    "plan must target the SAME code area; work spanning two repos belongs in two plans.\n"
    "Size every job to complete within ONE agent turn quota (default 15 minutes of "
    "focused work). Split anything larger into sequential jobs along natural seams. "
    "A job that overruns its quota is auto-continued a limited number of times as a "
    "safety net - continuation is the safety net, not the plan; never size a job "
    "assuming it will get extra turns."
)

# Appended to the graph architect prompt so targets name real areas instead of
# invented paths. Kept out of GRAPH_ARCHITECT_SYSTEM because it is per-project.
_NO_CODE_AREAS_NOTE = (
    "\nPROJECT WORK AREAS: this project has no registered code areas, so every "
    "job's target must be \"ops\"."
)


def _code_areas_block(code_areas: list[str]) -> str:
    if not code_areas:
        return _NO_CODE_AREAS_NOTE
    listed = ", ".join(f'"{a}"' for a in code_areas)
    return (
        f"\nPROJECT WORK AREAS: code areas: {listed} "
        '("." means the project root is itself the repo), plus "ops" for '
        "non-repo work. A job's target must be exactly one of these values."
    )


def architect_system(*, graph: bool = False, code_areas: list[str] | None = None) -> str:
    if not graph:
        return ARCHITECT_SYSTEM
    return GRAPH_ARCHITECT_SYSTEM + _code_areas_block(code_areas or [])


def _cron_field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    for part in field.split(","):
        step = 1
        body = part
        if "/" in part:
            body, step_s = part.split("/", 1)
            step = _as_int(step_s)
        if body in ("*", ""):
            rlo, rhi = lo, hi
        elif "-" in body:
            a, b = body.split("-", 1)
            rlo, rhi = _as_int(a), _as_int(b)
        else:
            rlo = rhi = _as_int(body)
        if rlo <= value <= rhi and (value - rlo) % step == 0:
            return True
    return False


_CRON_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # min hour dom mon dow


def _cron_field_ok(field: str, lo: int, hi: int) -> bool:
    """Strict per-field validation: every comma part must be a parseable *, */n,
    a-b or single value within bounds. Raises nothing — returns False on any
    malformed token (non-numeric, zero/negative step, out-of-range, inverted range)."""
    for part in field.split(","):
        body = part
        if "/" in part:
            body, step_s = part.split("/", 1)
            if not step_s.isdigit() or _as_int(step_s) <= 0:
                return False
        if body in ("*", ""):
            continue
        if "-" in body:
            a, b = body.split("-", 1)
            if not (a.lstrip("-").isdigit() and b.lstrip("-").isdigit()):
                return False
            if not (lo <= _as_int(a) <= _as_int(b) <= hi):
                return False
        elif not (body.isdigit() and lo <= _as_int(body) <= hi):
            return False
    return True


def cron_valid(cron: str) -> bool:
    """True only for a well-formed 5-field cron we can evaluate without raising.
    Used to reject bad crons at create/update time so a typo can't poison the
    scheduler loop or the dashboard (which both evaluate stored crons)."""
    fields = cron.split()
    if len(fields) != 5:
        return False
    return all(_cron_field_ok(f, lo, hi) for f, (lo, hi) in zip(fields, _CRON_BOUNDS))


def cron_matches(cron: str, dt: "datetime") -> bool:
    """Minimal 5-field cron matcher (min hour day-of-month month day-of-week).
    Supports *, */n, a-b, a,b and single values. Sunday is 0 (and 7). No deps —
    avoids pulling in croniter; we tick once a minute and ask 'does now match?'.
    Defensive: a malformed (e.g. legacy) cron never raises — it simply never
    matches, so one bad row can't abort the scheduler tick or crash an endpoint."""
    fields = cron.split()
    if len(fields) != 5:
        return False
    minute, hour, dom, mon, dow = fields
    weekday = (dt.weekday() + 1) % 7  # Python Mon=0 -> cron Sun=0
    try:
        return (
            _cron_field_matches(minute, dt.minute, 0, 59)
            and _cron_field_matches(hour, dt.hour, 0, 23)
            and _cron_field_matches(dom, dt.day, 1, 31)
            and _cron_field_matches(mon, dt.month, 1, 12)
            and (_cron_field_matches(dow, weekday, 0, 6) or _cron_field_matches(dow, 7 if weekday == 0 else weekday, 0, 7))
        )
    except (ValueError, ZeroDivisionError):
        return False


_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def cadence_human(cron: str) -> str:
    """A friendly label for a cron expression (best-effort; falls back to raw)."""
    f = cron.split()
    if len(f) != 5:
        return cron
    mn, hr, dom, mon, dow = f
    if cron.strip() == "* * * * *":
        return "Every minute"
    if mn.startswith("*/") and (hr, dom, mon, dow) == ("*", "*", "*", "*") and mn[2:].isdigit():
        return f"Every {mn[2:]} min"
    if mn == "0" and (hr, dom, mon, dow) == ("*", "*", "*", "*"):
        return "Hourly"
    if mn.isdigit() and hr.isdigit():
        at = f"{_as_int(hr):02d}:{_as_int(mn):02d}"
        if (dom, mon, dow) == ("*", "*", "*"):
            return f"Daily · {at}"
        if dom == "*" and mon == "*" and dow != "*":
            days = ", ".join(_WEEKDAYS[_as_int(x) % 7] for x in dow.replace("-", ",").split(",") if x.isdigit())
            return f"{days or dow} · {at}"
    return cron


def next_cron_after(cron: str, now: "datetime") -> "datetime | None":
    """The next minute (after `now`) that `cron` fires, scanning up to 90 days.
    Returns None if no match in that window (or an invalid cron)."""
    if not cron_valid(cron):
        return None
    t = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(90 * 24 * 60):
        if cron_matches(cron, t):
            return t
        t += timedelta(minutes=1)
    return None


def parse_blueprint(text: str) -> dict[str, Any]:
    """Parse the architect agent's JSON reply into a normalized (unsaved) workflow
    draft. Tolerant of surrounding prose/code fences by slicing to the outermost
    JSON object."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in architect reply")
    data = _json.loads(text[start : end + 1])
    draft = {
        "name": (data.get("name") or "Workflow").strip(),
        "description": data.get("description") or "",
        "category": data.get("category") or "other",
    }
    graph_data = data.get("graph")
    if graph_data is None and data.get("nodes") is not None:
        graph_data = {"nodes": data.get("nodes"), "edges": data.get("edges") or []}
    if graph_data is not None:
        draft["graph"] = normalize_graph(graph_data)
        draft["steps"] = []
    else:
        draft["steps"] = normalize_steps(data.get("steps") or [])
    return draft
