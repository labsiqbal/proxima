from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from proxima_api import app_settings, features, wiki_memory, workflows
from proxima_api.directory_handles import directory_identity_for_path
from proxima_api.main import _config_from_env, create_app
from proxima_api.settings import normalize_config


def _app(tmp_path, **config):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        **config,
    })


def _client_with_project(tmp_path, **config):
    app = _app(tmp_path, **config)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"}).json()
    return app, client, headers, Path(project["path"])


def _assert_disabled(response, feature):
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": features.disabled_payload(feature)}


def _counts(app):
    return {
        table: app.state.db.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        for table in ("sessions", "messages", "runs", "prompt_collaborations")
    }


def test_public_config_defaults_shipped_features_on(tmp_path):
    # Master ships on now that its acceptance gates have passed (ADR-0039).
    app = _app(tmp_path)
    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json()["features"] == {
        "design_studio": True,
        "workflow_graph": True,
        "repo_worktrees": True,
        "master_orchestrator": True,
    }


def test_public_config_reports_explicit_boot_opt_out(tmp_path):
    # The worktree flag is the slice-4 escape hatch: an explicit off must win
    # over the on-by-default.
    app = _app(
        tmp_path,
        feature_design_studio=True,
        feature_workflow_graph=True,
        feature_repo_worktrees=False,
    )
    assert TestClient(app).get("/api/config").json()["features"] == {
        "design_studio": True,
        "workflow_graph": True,
        "repo_worktrees": False,
        "master_orchestrator": True,
    }


def test_workflow_graph_environment_flag_reaches_asgi_config(monkeypatch):
    monkeypatch.setenv("PROXIMA_FEATURE_WORKFLOW_GRAPH", "1")

    assert _config_from_env()["feature_workflow_graph"]


def test_graph_semantic_egress_defaults_off_and_requires_explicit_opt_in(
    monkeypatch,
):
    monkeypatch.delenv("PROXIMA_GRAPH_SEMANTIC_EGRESS", raising=False)
    assert _config_from_env()["graph_semantic_egress_enabled"] is False
    assert normalize_config()["graph_semantic_egress_enabled"] is False

    monkeypatch.setenv("PROXIMA_GRAPH_SEMANTIC_EGRESS", "1")
    assert _config_from_env()["graph_semantic_egress_enabled"] is True
    assert normalize_config(
        {"graph_semantic_egress_enabled": "true"}
    )["graph_semantic_egress_enabled"] is True


def test_master_environment_flag_reaches_production_serve_entrypoint(tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "PROXIMA_DB_PATH": str(tmp_path / "serve.db"),
            "PROXIMA_WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "PROXIMA_WEB_DIST": str(tmp_path / "web-dist"),
            "PROXIMA_FEATURE_MASTER_ORCHESTRATOR": "1",
            "PROXIMA_UPDATE_CHECK": "0",
        }
    )
    serve_path = Path(__file__).parents[1] / "scripts" / "serve.py"
    code = (
        "import runpy, sys; "
        "from fastapi.testclient import TestClient; "
        "namespace = runpy.run_path(sys.argv[1]); "
        "response = TestClient(namespace['app']).get('/api/config'); "
        "print(response.json()['features']['master_orchestrator'])"
    )

    result = subprocess.run(
        [sys.executable, "-c", code, str(serve_path)],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.stdout.strip() == "True"


def test_programmatic_zero_values_do_not_enable_features():
    config = normalize_config({"feature_design_studio": "false", "feature_workflow_graph": "0", "feature_repo_worktrees": "0", "feature_master_orchestrator": "0"})
    assert features.public_flags(config) == {
        "design_studio": False,
        "workflow_graph": False,
        "repo_worktrees": False,
        "master_orchestrator": False,
    }


def test_disabled_master_runtime_leaves_master_and_owned_task_runs_queued(
    tmp_path,
):
    # Master is on by default, so this feature-off path opts out explicitly.
    app = _app(tmp_path, feature_master_orchestrator=False)
    client = TestClient(app)
    assert client.post("/auth/auto").status_code == 200
    owner = app.state.worker_db.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()
    worker_profile = app.state.worker_db.execute(
        "SELECT * FROM profiles WHERE system_kind IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    master_profile_id = int(
        app.state.worker_db.execute(
            "INSERT INTO profiles("
            "user_id, slug, name, hermes_home, runner_id, system_kind"
            ") VALUES (?, 'persisted-master', 'Master', ?, ?, 'master')",
            (
                owner["id"],
                str(tmp_path / "unprovisioned-master-home"),
                worker_profile["runner_id"],
            ),
        ).lastrowid
    )
    master_session_id = int(
        app.state.worker_db.execute(
            "INSERT INTO sessions("
            "title, owner_user_id, profile_id, runner_id, mode"
            ") VALUES ('Master', ?, ?, ?, 'master')",
            (
                owner["id"],
                master_profile_id,
                worker_profile["runner_id"],
            ),
        ).lastrowid
    )
    container_root = tmp_path / "feature-off-container"
    container_root.mkdir()
    container_id = int(
        app.state.worker_db.execute(
            "INSERT INTO projects("
            "slug, name, path, path_identity, owner_user_id"
            ") VALUES ('feature-off', 'Feature off', ?, ?, ?)",
            (
                str(container_root),
                directory_identity_for_path(container_root),
                owner["id"],
            ),
        ).lastrowid
    )
    target_area_id = int(
        app.state.worker_db.execute(
            "INSERT INTO project_areas(project_id, kind, rel_path, source) "
            "VALUES (?, 'ops', '.', 'manual')",
            (container_id,),
        ).lastrowid
    )
    worker_session_id = int(
        app.state.worker_db.execute(
            "INSERT INTO sessions("
            "title, project_id, owner_user_id, profile_id, runner_id, mode"
            ") VALUES ('Master-owned Task', ?, ?, ?, ?, 'chat')",
            (
                container_id,
                owner["id"],
                worker_profile["id"],
                worker_profile["runner_id"],
            ),
        ).lastrowid
    )
    job_id = int(
        app.state.worker_db.execute(
            "INSERT INTO jobs("
            "project_id, session_id, title, status, target_area_id, "
            "origin_master_session_id, created_by"
            ") VALUES (?, ?, 'Master-owned Task', 'queued', ?, ?, ?)",
            (
                container_id,
                worker_session_id,
                target_area_id,
                master_session_id,
                owner["id"],
            ),
        ).lastrowid
    )
    app.state.worker_db.execute(
        "UPDATE sessions SET job_id = ? WHERE id = ?",
        (job_id, worker_session_id),
    )
    run_id = int(
        app.state.worker_db.execute(
            "INSERT INTO runs("
            "session_id, user_id, profile_id, runner_id, kind, status, prompt"
            ") VALUES (?, ?, ?, ?, 'job', 'queued', 'preserve queued intent')",
            (
                worker_session_id,
                owner["id"],
                worker_profile["id"],
                worker_profile["runner_id"],
            ),
        ).lastrowid
    )
    app.state.worker_db.execute(
        "INSERT INTO task_delegations("
        "origin_session_id, origin_focus_captured, container_id, "
        "target_area_id, job_id, routing_mode, created_by, idempotency_key, "
        "idempotency_identity, request_fingerprint, start_requested, start_state"
        ") VALUES (?, 1, ?, ?, ?, 'explicit', ?, 'feature-off', "
        "'feature-off-identity', 'feature-off-fingerprint', 1, 'pending')",
        (
            master_session_id,
            container_id,
            target_area_id,
            job_id,
            owner["id"],
        ),
    )

    assert app.state.task_delegation.resume_committed() == []
    assert app.state.worker.claim_run() is None
    assert app.state.worker_db.execute(
        "SELECT status FROM runs WHERE id = ?", (run_id,)
    ).fetchone()["status"] == "queued"
    assert app.state.worker_db.execute(
        "SELECT status, started_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "queued"
    delegation = app.state.worker_db.execute(
        "SELECT start_state, start_attempts FROM task_delegations WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert dict(delegation) == {
        "start_state": "pending",
        "start_attempts": 0,
    }

    app.state.config["feature_master_orchestrator"] = True
    claimed = app.state.worker.claim_run()
    assert claimed is not None
    assert claimed["id"] == run_id


def test_disabled_commands_are_omitted_and_rejected(tmp_path):
    _app_obj, client, headers, _root = _client_with_project(tmp_path, feature_design_studio=False)
    catalog = client.get("/api/commands/catalog", headers=headers).json()
    commands = {item["name"] for group in catalog["groups"] for item in group["commands"]}

    assert "/image" in commands
    assert "/design" not in commands
    _assert_disabled(
        client.post("/api/commands/execute", headers=headers, json={"command": "/design draft"}),
        features.DESIGN_STUDIO,
    )


@pytest.mark.parametrize("prompt_mode", ["brainstorm", "debate"])
@pytest.mark.parametrize(
    ("message", "feature"),
    [("/design launch card", features.DESIGN_STUDIO)],
)
def test_disabled_prompt_modes_cannot_bypass_guards_or_write_rows(tmp_path, monkeypatch, prompt_mode, message, feature):
    app, client, headers, _root = _client_with_project(tmp_path, feature_design_studio=False)
    session_id = client.post("/api/sessions", headers=headers, json={"title": "chat", "project_slug": "demo"}).json()["id"]
    before = _counts(app)

    response = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": message, "prompt_mode": prompt_mode},
    )

    _assert_disabled(response, feature)
    assert _counts(app) == before


def test_disabled_chat_send_and_design_session_create_have_no_side_effects(tmp_path):
    app, client, headers, _root = _client_with_project(tmp_path, feature_design_studio=False)
    before = _counts(app)

    _assert_disabled(
        client.post("/api/sessions", headers=headers, json={"title": "Design", "project_slug": "demo", "mode": "design"}),
        features.DESIGN_STUDIO,
    )
    assert _counts(app) == before


def test_disabled_direct_routes_stop_before_provider_or_file_side_effects(tmp_path, monkeypatch):
    app, client, headers, root = _client_with_project(tmp_path, feature_design_studio=False)
    design_requests = [
        client.post("/api/projects/demo/designs/from-image", headers=headers, json={"path": "missing.png"}),
        client.post("/api/projects/demo/design/image", headers=headers, json={"prompt": "blocked"}),
        client.get("/api/projects/demo/design/image-models", headers=headers),
    ]

    for response in design_requests:
        _assert_disabled(response, features.DESIGN_STUDIO)
    assert not (root / "artifacts" / "design").exists()


def test_higgsfield_image_settings_remain_available_without_exposing_video_controls(tmp_path):
    app, client, headers, _root = _client_with_project(tmp_path)
    app_settings.set_json(app.state.db, app_settings.HIGGSFIELD_KEY, {
        "imagePolicy": "zero-credit-only",
        "imageModel": "old-image",
        "videoPolicy": "confirm-credits",
        "videoModel": "legacy-video",
    })

    settings = client.get("/api/settings/higgsfield", headers=headers)
    assert set(settings.json()["settings"]) == {"imagePolicy", "imageModel"}
    saved = client.put(
        "/api/settings/higgsfield",
        headers=headers,
        json={"imagePolicy": "ask-before-credits", "imageModel": "nano-banana"},
    )
    assert saved.status_code == 200
    assert set(saved.json()["settings"]) == {"imagePolicy", "imageModel"}
    stored = app_settings.get_json(app.state.db, app_settings.HIGGSFIELD_KEY)
    assert set(stored) == {"imagePolicy", "imageModel"}


def test_disabled_features_do_not_block_ordinary_artifact_reads(tmp_path):
    _app_obj, client, headers, root = _client_with_project(tmp_path)
    video = root / "ops" / "artifacts" / "media" / "videos" / "existing.mp4"
    scene = root / "ops" / "artifacts" / "design" / "existing" / "scene.json"
    video.parent.mkdir(parents=True, exist_ok=True)
    scene.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")
    scene.write_text(json.dumps({"id": "existing"}), encoding="utf-8")

    assert client.get("/api/projects/demo/raw", headers=headers, params={"path": "artifacts/media/videos/existing.mp4"}).content == b"mp4"
    read_scene = client.get("/api/projects/demo/file", headers=headers, params={"path": "artifacts/design/existing/scene.json"})
    assert read_scene.status_code == 200
    try:
        scene_payload = json.loads(read_scene.json()["content"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise AssertionError("scene response did not contain valid JSON") from exc
    assert scene_payload["id"] == "existing"


@pytest.mark.parametrize(
    ("session_mode", "prompt", "run_kind", "feature"),
    [
        ("design", "edit scene", "chat", features.DESIGN_STUDIO),
        ("chat", "execute graph node", "wf_node", features.WORKFLOW_GRAPH),
        ("chat", "architect graph", "workflow_graph_draft", features.WORKFLOW_GRAPH),
    ],
)
def test_worker_rejects_disabled_queued_work_before_runner_setup(
    tmp_path, monkeypatch, session_mode, prompt, run_kind, feature
):
    app, client, headers, _root = _client_with_project(tmp_path, feature_design_studio=False, feature_workflow_graph=False)
    owner = app.state.worker_db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    profile = app.state.worker_db.execute("SELECT * FROM profiles LIMIT 1").fetchone()
    project = app.state.worker_db.execute("SELECT id FROM projects WHERE slug = 'demo'").fetchone()["id"]
    session = app.state.worker_db.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, mode) VALUES (?, ?, ?, ?, ?, ?)",
        ("queued", project, owner, profile["id"], profile["runner_id"], session_mode),
    ).lastrowid
    run_id = app.state.worker_db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, hermes_home, kind) "
        "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
        (
            session,
            project,
            owner,
            profile["id"],
            profile["runner_id"],
            prompt,
            profile["hermes_home"],
            run_kind,
        ),
    ).lastrowid
    run = dict(app.state.worker_db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    graph_job_id = None
    if run_kind == "wf_node":
        graph_job_id = app.state.worker_db.execute(
            "INSERT INTO jobs(project_id, session_id, title, status, engine, graph, created_by) "
            "VALUES (?, ?, 'Graph', 'running', 'graph', '{\"nodes\":[],\"edges\":[]}', ?)",
            (project, session, owner),
        ).lastrowid
        app.state.worker_db.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (graph_job_id, session))
        app.state.worker_db.execute(
            "INSERT INTO node_states(job_id, node_id, status, run_id) VALUES (?, 'node-a', 'running', ?)",
            (graph_job_id, run_id),
        )
    monkeypatch.setattr("proxima_api.worker.runner_spec", lambda *_: pytest.fail("runner setup called"))

    asyncio.run(app.state.worker.execute_run(run))

    saved = app.state.worker_db.execute("SELECT status, error FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert dict(saved) == {"status": "failed", "error": f"feature_disabled:{feature}"}
    message_count = app.state.worker_db.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (session,)
    ).fetchone()["c"]
    assert message_count == 0
    if graph_job_id is not None:
        node = app.state.worker_db.execute(
            "SELECT status, error FROM node_states WHERE job_id = ?", (graph_job_id,)
        ).fetchone()
        job = app.state.worker_db.execute("SELECT status FROM jobs WHERE id = ?", (graph_job_id,)).fetchone()
        assert node["status"] == "failed"
        assert node["error"] == f"feature_disabled:{feature}"
        assert job["status"] == "review"


def test_disabled_design_session_secondary_actions_do_not_write(tmp_path):
    app, client, headers, root = _client_with_project(tmp_path, feature_design_studio=False)
    owner = app.state.db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    profile = app.state.db.execute("SELECT * FROM profiles LIMIT 1").fetchone()
    project = app.state.db.execute("SELECT id FROM projects WHERE slug = 'demo'").fetchone()["id"]
    session_id = app.state.db.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, mode) VALUES (?, ?, ?, ?, ?, 'design')",
        ("legacy design", project, owner, profile["id"], profile["runner_id"]),
    ).lastrowid
    message_id = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, author) VALUES (?, 'assistant', 'draft', 'agent')",
        (session_id,),
    ).lastrowid
    review_id = app.state.db.execute(
        "INSERT INTO message_reviews(source_message_id, session_id, status, revised_content) VALUES (?, ?, 'done', 'revised')",
        (message_id, session_id),
    ).lastrowid
    before = _counts(app)

    responses = [
        client.post(f"/api/sessions/{session_id}/messages", headers=headers, json={"role": "user", "content": "edit"}),
        client.post(f"/api/sessions/{session_id}/goal", headers=headers, json={"objective": "finish"}),
        client.post(f"/api/sessions/{session_id}/wiki-note/draft", headers=headers, json={}),
        client.post(f"/api/sessions/{session_id}/promote-workflow", headers=headers, json={}),
        client.post(f"/api/sessions/{session_id}/wiki-note/commit", headers=headers, json={"path": "blocked.md", "content": "blocked"}),
        client.post(f"/api/messages/{message_id}/reviews", headers=headers, json={"mode": "validate"}),
        client.post(f"/api/message-reviews/{review_id}/replace-answer", headers=headers),
        client.post(f"/api/message-reviews/{review_id}/restore-original", headers=headers),
        client.post(f"/api/message-reviews/{review_id}/ask-original", headers=headers, json={}),
    ]

    for response in responses:
        _assert_disabled(response, features.DESIGN_STUDIO)
    assert _counts(app) == before
    assert not (root / "wiki" / "blocked.md").exists()


def test_agent_guidance_omits_disabled_features(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    preamble = wiki_memory.build_run_preamble("Demo", "demo", wiki)
    capabilities = workflows.build_capability_preamble()

    for text in (preamble, capabilities):
        if text is None:
            raise AssertionError("agent guidance unexpectedly returned no text")
        assert "Design Studio" not in text
        assert "Video Studio" not in text
        assert "artifacts/design" not in text
        assert "artifacts/video" not in text
