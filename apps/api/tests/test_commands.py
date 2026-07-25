from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.commands import (
    build_skill_slash_commands,
    execute_command,
    normalize_command,
    reserved_command_names,
    skill_command_map,
    skill_slash_name,
)
from proxima_api.main import create_app


def test_normalize_command_alias_and_force_raw():
    assert normalize_command("status") == ("/status", "", False)
    assert normalize_command("/reset") == ("/new", "", False)
    assert normalize_command("//model sonnet") == ("/model", "sonnet", True)
    assert normalize_command("/masterplan\nship a CLI") == ("/masterplan", "ship a CLI", False)


def test_skill_slash_naming_reserved_and_collisions():
    reserved = reserved_command_names()
    assert skill_slash_name("grill-with-docs", reserved=reserved, used=set()) == "/grill-with-docs"
    # Built-in /help wins; skill becomes group-leaf.
    assert skill_slash_name("help", reserved=reserved, used=set(), group="ops") == "/ops-help"
    # Same leaf twice → second gets group-leaf then numeric suffix if needed.
    used = {"/review"}
    assert skill_slash_name("review", reserved=reserved, used=used, group="code") == "/code-review"
    cmds = build_skill_slash_commands(
        [
            {"id": "help", "name": "help", "description": "bad leaf", "group": "tools"},
            {"id": "bundled/masterplan", "name": "masterplan", "description": "first-class"},
            {"id": "grill-with-docs", "name": "grill", "description": "Grill with docs"},
            {"id": "cat/nested", "name": "nested", "description": "Nested", "group": "cat"},
        ]
    )
    names = {c.name: c.skill_id for c in cmds}
    assert "/masterplan" not in names  # first-class, not duplicated
    assert names["/grill-with-docs"] == "grill-with-docs"
    assert names["/tools-help"] == "help"
    assert "/help" not in names
    # MCP never appears
    assert all(c.surface == "proxima" for c in cmds)


def test_execute_skill_slash_agent_turn():
    user = {"username": "bob", "role": "member"}
    skill_map = {"/grill-with-docs": "grill-with-docs"}
    turn = execute_command(
        "/grill-with-docs tighten the brief",
        user=user,
        skill_map=skill_map,
    )
    assert turn["kind"] == "agent_turn"
    assert turn["skillId"] == "grill-with-docs"
    assert turn["runKind"] == "skill"
    assert "tighten the brief" in turn["message"]
    assert "Required skill: `grill-with-docs`" in turn["message"]
    bare = execute_command("/grill-with-docs", user=user, skill_map=skill_map)
    assert bare["kind"] == "agent_turn"
    assert "No freeform argument" in bare["message"]


def test_execute_command_surfaces():
    user = {"username": "bob", "role": "member"}
    assert execute_command("/help", user=user)["message"].startswith("Proxima commands")
    assert "Command router: ready" in execute_command("/status", user=user, project_slug="demo", runner_id="hermes")["message"]
    masterplan = execute_command("/masterplan build a durable CLI", user=user)
    assert masterplan["kind"] == "agent_turn"
    assert masterplan["skillId"] == "bundled/masterplan"
    assert masterplan["runKind"] == "masterplan"
    assert "build a durable CLI" in masterplan["message"]
    assert execute_command("/model", user=user)["surface"] == "ui-owned"
    assert execute_command("/clear", user=user)["surface"] == "terminal-only"
    assert execute_command("//model sonnet", user=user)["kind"] == "runner_raw"
    assert execute_command("/unknown", user=user)["surface"] == "unknown"


def test_command_endpoints_catalog_and_execute(tmp_path):
    # Bundle with an extra skill so catalog/execute can publish a dynamic slash.
    bundle = tmp_path / "bundle"
    for name in ("masterplan", "grill-with-docs"):
        (bundle / name).mkdir(parents=True)
        (bundle / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\n")

    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "bundled_skills_dir": str(bundle),
            "start_worker": False,
        }
    )
    client = TestClient(app)

    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    catalog = client.get("/api/commands/catalog", headers=headers)
    assert catalog.status_code == 200
    body = catalog.json()
    assert any(group["label"] == "Session" for group in body["groups"])
    planning = next(group for group in body["groups"] if group["label"] == "Planning")
    assert any(command["name"] == "/masterplan" for command in planning["commands"])
    # Dynamic skill slash from enabled (inherited) skills
    all_cmds = [c for g in body["groups"] for c in g["commands"]]
    skill_cmds = [c for c in all_cmds if c.get("skillId") == "bundled/grill-with-docs"]
    assert skill_cmds, "enabled skill should appear as a slash command"
    grill_name = skill_cmds[0]["name"]
    assert grill_name.startswith("/") and "grill-with-docs" in grill_name
    # MCP never in slash list
    assert all(c.get("surface") != "mcp" for c in all_cmds)

    executed = client.post(
        "/api/commands/execute",
        headers=headers,
        json={"command": "/status", "runner_id": "hermes"},
    )
    assert executed.status_code == 200
    assert "Command router: ready" in executed.json()["message"]

    client.post("/api/projects", headers=headers, json={"slug": "alpha", "name": "Alpha"})
    scoped = client.post(
        "/api/commands/execute",
        headers=headers,
        json={"command": "/status", "project_slug": "alpha", "runner_id": "codex"},
    )
    assert scoped.status_code == 200
    assert "Project: alpha" in scoped.json()["message"]
    assert "Runner: codex" in scoped.json()["message"]

    masterplan = client.post(
        "/api/commands/execute",
        headers=headers,
        json={"command": "/masterplan build a durable CLI", "project_slug": "alpha"},
    )
    assert masterplan.status_code == 200
    assert masterplan.json()["kind"] == "agent_turn"
    assert masterplan.json()["skillId"] == "bundled/masterplan"

    skill_turn = client.post(
        "/api/commands/execute",
        headers=headers,
        json={"command": f"{grill_name} ship it", "project_slug": "alpha"},
    )
    assert skill_turn.status_code == 200
    assert skill_turn.json()["kind"] == "agent_turn"
    assert skill_turn.json()["skillId"] == "bundled/grill-with-docs"
    assert "ship it" in skill_turn.json()["message"]

    # Custom skill roots API + rescan
    custom = tmp_path / "extra-skills"
    (custom / "from-custom").mkdir(parents=True)
    (custom / "from-custom" / "SKILL.md").write_text("---\nname: from-custom\ndescription: custom root skill\n---\n")
    put = client.put("/api/settings/skill-roots", headers=headers, json={"roots": [str(custom), str(tmp_path / "missing")]})
    assert put.status_code == 200
    assert str(custom) in put.json()["roots"]
    roots = client.get("/api/settings/skill-roots", headers=headers)
    assert roots.json()["roots"] == put.json()["roots"]

    profiles = client.get("/api/profiles", headers=headers).json()["profiles"]
    rid = profiles[0]["runner_id"]
    rescanned = client.post(f"/api/runners/{rid}/capabilities/rescan", headers=headers)
    assert rescanned.status_code == 200
    skill_ids = {s["id"] for s in rescanned.json()["skills"]}
    assert "from-custom" in skill_ids or "bundled/from-custom" in skill_ids or any("from-custom" in s for s in skill_ids)
    # Warnings for missing custom path are non-fatal
    assert isinstance(rescanned.json().get("warnings"), list)

    # Opt-out of a skill → it leaves the slash catalog
    client.patch(
        f"/api/profiles/{profiles[0]['id']}",
        headers=headers,
        json={"capabilities": {"skills": ["bundled/masterplan"], "mcp": []}},
    )
    catalog2 = client.get(
        f"/api/commands/catalog?profile_id={profiles[0]['id']}&rescan=1",
        headers=headers,
    )
    names2 = {c["name"] for g in catalog2.json()["groups"] for c in g["commands"]}
    skill_ids2 = {c.get("skillId") for g in catalog2.json()["groups"] for c in g["commands"]}
    assert "/masterplan" in names2
    assert "bundled/grill-with-docs" not in skill_ids2
    assert grill_name not in names2
