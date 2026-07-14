from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import app_settings, features, video_providers, wiki_memory, workflows
from proxima_api.main import create_app
from proxima_api.settings import normalize_config


def _app(tmp_path, **config):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        **config,
    })


def _client_with_project(tmp_path):
    app = _app(tmp_path)
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


def test_public_config_defaults_both_features_off(tmp_path):
    app = _app(tmp_path)
    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json()["features"] == {"video": False, "design_studio": False, "workflow_graph": False}


def test_public_config_reports_explicit_boot_opt_in(tmp_path):
    app = _app(tmp_path, feature_video=True, feature_design_studio=True)
    assert TestClient(app).get("/api/config").json()["features"] == {"video": True, "design_studio": True, "workflow_graph": False}


def test_programmatic_zero_values_do_not_enable_features():
    config = normalize_config({"feature_video": "0", "feature_design_studio": "false"})
    assert features.public_flags(config) == {"video": False, "design_studio": False, "workflow_graph": False}


def test_disabled_commands_are_omitted_and_rejected(tmp_path):
    _app_obj, client, headers, _root = _client_with_project(tmp_path)
    catalog = client.get("/api/commands/catalog", headers=headers).json()
    commands = {item["name"] for group in catalog["groups"] for item in group["commands"]}

    assert "/image" in commands
    assert commands.isdisjoint({"/video", "/video-studio", "/design"})
    _assert_disabled(
        client.post("/api/commands/execute", headers=headers, json={"command": "/design draft"}),
        features.DESIGN_STUDIO,
    )
    _assert_disabled(
        client.post("/api/commands/execute", headers=headers, json={"command": "//video draft"}),
        features.VIDEO,
    )


@pytest.mark.parametrize("prompt_mode", ["brainstorm", "debate"])
@pytest.mark.parametrize(
    ("message", "feature"),
    [("/video launch reel", features.VIDEO), ("/design launch card", features.DESIGN_STUDIO)],
)
def test_disabled_prompt_modes_cannot_bypass_guards_or_write_rows(tmp_path, monkeypatch, prompt_mode, message, feature):
    app, client, headers, _root = _client_with_project(tmp_path)
    session_id = client.post("/api/sessions", headers=headers, json={"title": "chat", "project_slug": "demo"}).json()["id"]
    monkeypatch.setattr(video_providers, "generate", lambda *a, **k: pytest.fail("provider called"))
    before = _counts(app)

    response = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": message, "prompt_mode": prompt_mode},
    )

    _assert_disabled(response, feature)
    assert _counts(app) == before


def test_disabled_chat_send_and_design_session_create_have_no_side_effects(tmp_path):
    app, client, headers, _root = _client_with_project(tmp_path)
    before = _counts(app)

    _assert_disabled(
        client.post("/api/chat/send", headers=headers, json={"project_slug": "demo", "message": "/video teaser"}),
        features.VIDEO,
    )
    _assert_disabled(
        client.post("/api/sessions", headers=headers, json={"title": "Design", "project_slug": "demo", "mode": "design"}),
        features.DESIGN_STUDIO,
    )
    assert _counts(app) == before


def test_disabled_direct_routes_stop_before_provider_or_file_side_effects(tmp_path, monkeypatch):
    app, client, headers, root = _client_with_project(tmp_path)
    provider_calls = []
    monkeypatch.setattr(video_providers, "test_connection", lambda *a, **k: provider_calls.append(1) or {"ok": True})

    video_requests = [
        client.get("/api/settings/video-gen", headers=headers),
        client.put("/api/settings/video-gen", headers=headers, json={"provider": "xai-oauth"}),
        client.post("/api/settings/video-gen/test", headers=headers, json={"provider": "xai-oauth"}),
        client.get("/api/projects/demo/videos", headers=headers),
        client.post("/api/projects/demo/videos", headers=headers, json={"title": "Blocked"}),
        client.post("/api/projects/demo/videos/nope/studio/start", headers=headers),
        client.get("/api/video-studio/no-token/demo/nope"),
        client.post("/api/projects/proxima-video__demo__nope/render", headers=headers, json={}),
        client.get("/api/render/nope", headers=headers),
        client.get("/api/events", headers=headers),
    ]
    design_requests = [
        client.post("/api/projects/demo/designs/from-image", headers=headers, json={"path": "missing.png"}),
        client.post("/api/projects/demo/design/image", headers=headers, json={"prompt": "blocked"}),
        client.get("/api/projects/demo/design/image-models", headers=headers),
    ]

    for response in video_requests:
        _assert_disabled(response, features.VIDEO)
    for response in design_requests:
        _assert_disabled(response, features.DESIGN_STUDIO)
    assert provider_calls == []
    assert app.state.db.execute("SELECT value FROM app_settings WHERE key = 'video_gen'").fetchone() is None
    assert not (root / "artifacts" / "video").exists()
    assert not (root / "artifacts" / "design").exists()


def test_higgsfield_image_settings_remain_available_without_exposing_video_controls(tmp_path):
    app, client, headers, _root = _client_with_project(tmp_path)

    settings = client.get("/api/settings/higgsfield", headers=headers)
    assert set(settings.json()["settings"]) == {"imagePolicy", "imageModel"}
    _assert_disabled(
        client.put("/api/settings/higgsfield", headers=headers, json={"videoModel": "ray-2"}),
        features.VIDEO,
    )
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
    video = root / "artifacts" / "media" / "videos" / "existing.mp4"
    scene = root / "artifacts" / "design" / "existing" / "scene.json"
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
        ("chat", "/video queued", "chat", features.VIDEO),
        ("design", "edit scene", "chat", features.DESIGN_STUDIO),
        ("chat", "execute graph node", "wf_node", features.WORKFLOW_GRAPH),
    ],
)
def test_worker_rejects_disabled_queued_work_before_runner_setup(
    tmp_path, monkeypatch, session_mode, prompt, run_kind, feature
):
    app, client, headers, _root = _client_with_project(tmp_path)
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
    monkeypatch.setattr("proxima_api.worker.runner_spec", lambda *_: pytest.fail("runner setup called"))

    asyncio.run(app.state.worker.execute_run(run))

    saved = app.state.worker_db.execute("SELECT status, error FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert dict(saved) == {"status": "failed", "error": f"feature_disabled:{feature}"}
    message_count = app.state.worker_db.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (session,)
    ).fetchone()["c"]
    assert message_count == 0


def test_disabled_design_session_secondary_actions_do_not_write(tmp_path):
    app, client, headers, root = _client_with_project(tmp_path)
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
