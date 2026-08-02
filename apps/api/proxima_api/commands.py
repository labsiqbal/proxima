from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .runner_specs import default_runner


MASTERPLAN_SKILL_ID = "bundled/masterplan"
MASTERPLAN_RUN_KIND = "masterplan"
SKILL_RUN_KIND = "skill"

# First-class skill commands that stay published even when the skill is
# temporarily opted out of a profile (temporary force-on at run time).
FIRST_CLASS_SKILL_COMMANDS: dict[str, str] = {
    MASTERPLAN_SKILL_ID: "/masterplan",
}


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    description: str
    group: str
    surface: str
    unavailable_message: str | None = None
    skill_id: str | None = None


COMMANDS: tuple[CommandDefinition, ...] = (
    CommandDefinition("/help", "Show Proxima chat commands", "Session", "proxima"),
    CommandDefinition("/status", "Show current user/project/runner status", "Session", "proxima"),
    CommandDefinition("/new", "Start a new session draft", "Session", "proxima"),
    CommandDefinition("/session", "Show current session context", "Session", "proxima"),
    CommandDefinition("/project", "Show or select project context", "Project", "proxima"),
    CommandDefinition("/runner", "Show or switch active runner", "Runner", "proxima"),
    CommandDefinition("/goal", "Autonomous goal loop — agent works across turns until done", "Session", "proxima"),
    CommandDefinition("/masterplan", "Turn a product idea into an execution-ready masterplan package", "Planning", "proxima", skill_id=MASTERPLAN_SKILL_ID),
    CommandDefinition("/image", "Generate an image with the selected image provider (Settings → Image generation)", "Media", "proxima"),
    CommandDefinition("/design", "Create a Design Studio draft from a brief", "Media", "proxima"),
    CommandDefinition("/model", "Open/select model via UI", "Runner", "ui-owned", "/model is managed by Proxima model picker, not raw chat."),
    CommandDefinition("/clear", "Terminal-only clear screen command", "Unavailable", "terminal-only", "/clear is terminal-only. Use /new or the Sessions sidebar in Proxima."),
    CommandDefinition("/tools", "Terminal-only toolset command", "Unavailable", "terminal-only", "/tools is terminal-only. Use Runners/Settings in Proxima."),
)

ALIASES = {
    "/reset": "/new",
    "/runners": "/runner",
    "/gambar": "/image",
    "/image-studio": "/design",
    "/design-studio": "/design",
}


def normalize_command(raw: str) -> tuple[str, str, bool]:
    text = raw.strip()
    force_raw = text.startswith("//")
    if force_raw:
        text = "/" + text[2:]
    if not text.startswith("/"):
        text = "/" + text
    parts = text.split(maxsplit=1)
    name = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    name = name.lower()
    name = ALIASES.get(name, name)
    return name, arg.strip(), force_raw


def reserved_command_names() -> set[str]:
    """Built-in Proxima command names (+ aliases) that always win collisions."""
    names = {cmd.name for cmd in COMMANDS}
    names.update(ALIASES.keys())
    names.update(ALIASES.values())
    return names


_LEAF_RE = re.compile(r"[^a-z0-9]+")


def skill_leaf_name(skill_id: str) -> str:
    """Leaf segment of a skill id, sanitized for slash command use."""
    leaf = str(skill_id or "").rsplit("/", 1)[-1].strip().lower()
    leaf = _LEAF_RE.sub("-", leaf).strip("-")
    return leaf or "skill"


def skill_slash_name(
    skill_id: str,
    *,
    reserved: set[str],
    used: set[str],
    group: str | None = None,
) -> str:
    """Pick `/leaf` or, on collision with reserved/used names, `/group-leaf`.

    Built-in Proxima commands always win the reserved set. First-class skill
    commands (e.g. `/masterplan`) are reserved so the bundled skill is not
    double-listed under a second name.
    """
    leaf = skill_leaf_name(skill_id)
    candidate = f"/{leaf}"
    if candidate not in reserved and candidate not in used:
        return candidate
    # Collision → group-leaf (group from skill id prefix or explicit group).
    if group:
        g = _LEAF_RE.sub("-", str(group).strip().lower()).strip("-") or "skill"
    elif "/" in str(skill_id):
        g = skill_leaf_name(str(skill_id).rsplit("/", 1)[0])
    else:
        g = "skill"
    candidate = f"/{g}-{leaf}"
    if candidate not in reserved and candidate not in used:
        return candidate
    # Last resort: numeric suffix so two identical leaves never collide.
    n = 2
    while f"{candidate}-{n}" in reserved or f"{candidate}-{n}" in used:
        n += 1
    return f"{candidate}-{n}"


def enabled_skills(
    skills: list[dict[str, Any]],
    selection: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Filter detected skills by profile selection (None = inherit all)."""
    if selection is None:
        return list(skills)
    raw = selection.get("skills") if isinstance(selection, dict) else None
    if not isinstance(raw, list):
        return list(skills)
    wanted = {str(x) for x in raw}
    return [s for s in skills if str(s.get("id") or "") in wanted]


def build_skill_slash_commands(
    skills: list[dict[str, Any]],
    selection: dict[str, Any] | None = None,
) -> list[CommandDefinition]:
    """Enabled skills → Proxima slash commands (not MCP).

    Naming: leaf; reserved built-ins win; collisions become group-leaf.
    `/masterplan` stays first-class and is not duplicated from the skill list.
    """
    reserved = reserved_command_names()
    # First-class skill slash names are reserved so the dynamic catalog skips them.
    reserved.update(FIRST_CLASS_SKILL_COMMANDS.values())
    used: set[str] = set()
    out: list[CommandDefinition] = []
    for skill in enabled_skills(skills, selection):
        sid = str(skill.get("id") or "").strip()
        if not sid:
            continue
        if sid in FIRST_CLASS_SKILL_COMMANDS:
            continue  # published as a first-class built-in
        name = skill_slash_name(
            sid,
            reserved=reserved,
            used=used,
            group=skill.get("group"),
        )
        used.add(name)
        desc = (skill.get("description") or skill.get("name") or sid or "").strip()
        if len(desc) > 160:
            desc = desc[:157] + "…"
        if not desc:
            desc = f"Run the {sid} skill"
        out.append(
            CommandDefinition(
                name=name,
                description=desc,
                group="Skills",
                surface="proxima",
                skill_id=sid,
            )
        )
    return out


def skill_command_map(
    skills: list[dict[str, Any]],
    selection: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Map slash name → skill id for enabled skills (+ first-class masterplan)."""
    # FIRST_CLASS is skill_id → name; invert for the execute map.
    by_name = {name: sid for sid, name in FIRST_CLASS_SKILL_COMMANDS.items()}
    for cmd in build_skill_slash_commands(skills, selection):
        if cmd.skill_id:
            by_name[cmd.name] = cmd.skill_id
    return by_name


def command_catalog(
    *,
    skills: list[dict[str, Any]] | None = None,
    selection: dict[str, Any] | None = None,
) -> dict:
    """Built-in commands plus optional per-profile skill slash entries."""
    groups: dict[str, list[dict]] = {}
    for cmd in COMMANDS:
        groups.setdefault(cmd.group, []).append(_catalog_entry(cmd))
    if skills is not None:
        for cmd in build_skill_slash_commands(skills, selection):
            groups.setdefault(cmd.group, []).append(_catalog_entry(cmd))
    return {"groups": [{"label": label, "commands": commands} for label, commands in groups.items()]}


def _catalog_entry(cmd: CommandDefinition) -> dict:
    return {
        "name": cmd.name,
        "description": cmd.description,
        "surface": cmd.surface,
        "unavailableMessage": cmd.unavailable_message,
        "skillId": cmd.skill_id,
    }


def find_command(name: str) -> CommandDefinition | None:
    return next((cmd for cmd in COMMANDS if cmd.name == name), None)


def _masterplan_prompt(idea: str) -> str:
    owner_ask = idea.strip()
    if owner_ask:
        next_step = (
            "Use the owner's idea below as the input to Phase 1. Start the "
            "masterplan pipeline now and continue skill-native, including its "
            "clarification and review gates."
        )
    else:
        owner_ask = "No idea was supplied with the command."
        next_step = (
            "The owner invoked the command without an idea. Ask the owner for their "
            "product idea as the first skill-native intake question, then continue "
            "the masterplan pipeline from Phase 1."
        )
    return f"""# Proxima command: /masterplan

Required skill: `{MASTERPLAN_SKILL_ID}`

Load and follow the bundled masterplan skill as the controlling methodology for this turn and subsequent turns in this session. Do not substitute generic planning, a status reply, or a Design Studio canvas. The deliverable remains the masterplan package folder and artifacts defined by the skill.

{next_step}

## Owner request

{owner_ask}
"""


def _skill_prompt(command_name: str, skill_id: str, arg: str) -> str:
    owner_ask = arg.strip()
    if owner_ask:
        next_step = (
            "Use the owner's freeform argument below as the skill input. Start now "
            "and follow the skill's native methodology, including any clarification "
            "or review gates it defines."
        )
    else:
        owner_ask = "No freeform argument was supplied with the command."
        next_step = (
            "The owner invoked the command without extra arguments. If the skill "
            "needs an input, ask for it as the first skill-native question, then continue."
        )
    return f"""# Proxima command: {command_name}

Required skill: `{skill_id}`

Load and follow the required skill as the controlling methodology for this turn. Do not substitute a generic status reply or invent a different workflow. Prefer the skill's own structure and deliverables.

{next_step}

## Owner request

{owner_ask}
"""


def agent_turn_for_command(
    raw_command: str,
    *,
    skill_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Resolve slash commands that intentionally become real agent turns.

    The command endpoint and chat run route share this expansion so the catalog
    action cannot drift from what the agent actually receives.

    `skill_map` maps `/slash-name` → skill id for the active profile's enabled
    skills (built by `skill_command_map`). `/masterplan` is always recognized.
    """
    name, arg, force_raw = normalize_command(raw_command)
    if force_raw:
        return None

    if name == "/masterplan":
        display = f"/masterplan{(' ' + arg) if arg else ''}"
        return {
            "kind": "agent_turn",
            "surface": "proxima",
            "command": name,
            "skillId": MASTERPLAN_SKILL_ID,
            "runKind": MASTERPLAN_RUN_KIND,
            "message": _masterplan_prompt(arg),
            "displayMessage": display,
        }

    if skill_map and name in skill_map:
        skill_id = skill_map[name]
        display = f"{name}{(' ' + arg) if arg else ''}"
        return {
            "kind": "agent_turn",
            "surface": "proxima",
            "command": name,
            "skillId": skill_id,
            "runKind": SKILL_RUN_KIND,
            "message": _skill_prompt(name, skill_id, arg),
            "displayMessage": display,
        }
    return None


def execute_command(
    raw_command: str,
    *,
    user: dict,
    project_slug: str | None = None,
    runner_id: str | None = None,
    skill_map: dict[str, str] | None = None,
    skill_commands: list[CommandDefinition] | None = None,
) -> dict:
    name, arg, force_raw = normalize_command(raw_command)

    if force_raw:
        return {
            "kind": "runner_raw",
            "command": name,
            "arg": arg,
            "message": f"Reserved raw runner passthrough: {name}{(' ' + arg) if arg else ''}",
        }

    agent_turn = agent_turn_for_command(raw_command, skill_map=skill_map)
    if agent_turn is not None:
        return agent_turn

    cmd = find_command(name)
    if not cmd and skill_commands:
        cmd = next((c for c in skill_commands if c.name == name), None)
    if not cmd:
        return {
            "kind": "system_message",
            "surface": "unknown",
            "message": f"Unknown command: {name}. Use /help to see Proxima commands. Use //{name.lstrip('/')} to reserve raw runner passthrough.",
        }

    if cmd.surface in {"terminal-only", "ui-owned"}:
        return {
            "kind": "system_message",
            "surface": cmd.surface,
            "message": cmd.unavailable_message or f"{name} is not available in chat.",
        }

    if name == "/help":
        names = [c.name for c in COMMANDS if c.surface == "proxima"]
        if skill_commands:
            names.extend(c.name for c in skill_commands if c.surface == "proxima")
        return {
            "kind": "system_message",
            "surface": "proxima",
            "message": f"Proxima commands: {', '.join(names)}. Use //command for raw runner passthrough.",
        }

    if name == "/status":
        return {
            "kind": "system_message",
            "surface": "proxima",
            "message": f"Owner: {user['username']}. Project: {project_slug or 'none'}. Runner: {runner_id or default_runner()}. Command router: ready.",
        }

    if name == "/new":
        return {"kind": "new_session", "surface": "proxima", "message": "New session draft ready."}

    if name == "/session":
        return {"kind": "system_message", "surface": "proxima", "message": f"Session context: project={project_slug or 'none'}, runner={runner_id or default_runner()}."}

    if name == "/project":
        if arg:
            return {"kind": "select_project", "surface": "proxima", "projectSlug": arg, "message": f"Project switch requested: {arg}"}
        return {"kind": "system_message", "surface": "proxima", "message": f"Current project: {project_slug or 'none'}."}

    if name == "/runner":
        if arg:
            return {"kind": "select_runner", "surface": "proxima", "runnerId": arg, "message": f"Runner switch requested: {arg}"}
        return {"kind": "system_message", "surface": "proxima", "message": f"Current runner: {runner_id or default_runner()}."}

    return {"kind": "system_message", "surface": "proxima", "message": f"Executed {name}."}
