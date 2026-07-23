from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import features
from .runner_specs import default_runner


MASTERPLAN_SKILL_ID = "bundled/masterplan"
MASTERPLAN_RUN_KIND = "masterplan"


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    description: str
    group: str
    surface: str
    unavailable_message: str | None = None
    feature: str | None = None


COMMANDS: tuple[CommandDefinition, ...] = (
    CommandDefinition("/help", "Show Proxima chat commands", "Session", "proxima"),
    CommandDefinition("/status", "Show current user/project/runner status", "Session", "proxima"),
    CommandDefinition("/new", "Start a new session draft", "Session", "proxima"),
    CommandDefinition("/session", "Show current session context", "Session", "proxima"),
    CommandDefinition("/project", "Show or select project context", "Project", "proxima"),
    CommandDefinition("/runner", "Show or switch active runner", "Runner", "proxima"),
    CommandDefinition("/goal", "Autonomous goal loop — agent works across turns until done", "Session", "proxima"),
    CommandDefinition("/masterplan", "Turn a product idea into an execution-ready masterplan package", "Planning", "proxima"),
    CommandDefinition("/image", "Generate an image with the selected image provider (Settings → Image generation)", "Media", "proxima"),
    CommandDefinition("/design", "Create a Design Studio draft from a brief", "Media", "proxima", feature=features.DESIGN_STUDIO),
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


def _available_commands(config: dict[str, Any] | None) -> tuple[CommandDefinition, ...]:
    return tuple(cmd for cmd in COMMANDS if not cmd.feature or features.enabled(config, cmd.feature))


def command_catalog(config: dict[str, Any] | None = None) -> dict:
    groups: dict[str, list[dict]] = {}
    for cmd in _available_commands(config):
        groups.setdefault(cmd.group, []).append(
            {
                "name": cmd.name,
                "description": cmd.description,
                "surface": cmd.surface,
                "unavailableMessage": cmd.unavailable_message,
            }
        )
    return {"groups": [{"label": label, "commands": commands} for label, commands in groups.items()]}


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


def agent_turn_for_command(raw_command: str) -> dict[str, Any] | None:
    """Resolve slash commands that intentionally become real agent turns.

    The command endpoint and chat run route share this expansion so the catalog
    action cannot drift from what the agent actually receives.
    """
    name, arg, force_raw = normalize_command(raw_command)
    if force_raw or name != "/masterplan":
        return None
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


def execute_command(
    raw_command: str,
    *,
    user: dict,
    project_slug: str | None = None,
    runner_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict:
    name, arg, force_raw = normalize_command(raw_command)
    features.require_command(config, name)

    if force_raw:
        return {
            "kind": "runner_raw",
            "command": name,
            "arg": arg,
            "message": f"Reserved raw runner passthrough: {name}{(' ' + arg) if arg else ''}",
        }

    cmd = find_command(name)
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
        names = ", ".join(c.name for c in _available_commands(config) if c.surface == "proxima")
        return {"kind": "system_message", "surface": "proxima", "message": f"Proxima commands: {names}. Use //command for raw runner passthrough."}

    agent_turn = agent_turn_for_command(raw_command)
    if agent_turn is not None:
        return agent_turn

    if name == "/status":
        return {
            "kind": "system_message",
            "surface": "proxima",
            "message": f"User: {user['username']} ({user['role']}). Project: {project_slug or 'none'}. Runner: {runner_id or default_runner()}. Command router: ready.",
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
