from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.artifacts import artifacts_for_output_links, scan_project_artifacts
from proxima_api.main import create_app


def test_scan_project_artifacts_for_chat_result_links(tmp_path):
    root = tmp_path / "project"
    (root / "artifacts" / "design" / "launch").mkdir(parents=True)
    (root / "artifacts" / "design" / "launch" / "scene.json").write_text(json.dumps({"id": "launch", "title": "Launch Post"}))
    (root / "artifacts" / "media" / "videos").mkdir(parents=True)
    (root / "artifacts" / "media" / "videos" / "launch-reel.mp4").write_bytes(b"mp4")
    (root / "artifacts" / "video" / "legacy-editable").mkdir(parents=True)
    (root / "artifacts" / "video" / "legacy-editable" / "index.html").write_text("<main>legacy studio shell</main>")
    (root / "artifacts" / "video" / "legacy-editable" / "brief.json").write_text("{}")
    (root / "artifacts" / "video" / "legacy-editable" / "render.webm").write_bytes(b"webm")
    (root / "artifacts" / "images").mkdir(parents=True)
    (root / "artifacts" / "images" / "hero.png").write_bytes(b"png")
    (root / "reports").mkdir()
    (root / "reports" / "brief.pdf").write_bytes(b"pdf")
    (root / "site").mkdir()
    (root / "site" / "package.json").write_text(json.dumps({"scripts": {"dev": "astro dev"}, "dependencies": {"astro": "^5.0.0"}}))

    artifacts = scan_project_artifacts(root, time.time() - 60)
    links = artifacts_for_output_links(artifacts, "alpha")

    by_type = {a["type"]: a for a in links}
    assert by_type["design"]["id"] == "launch"
    assert by_type["design"]["project_slug"] == "alpha"
    assert by_type["video-file"]["path"] == "artifacts/media/videos/launch-reel.mp4"
    assert by_type["video-file"]["project_slug"] == "alpha"
    assert by_type["image"]["path"] == "artifacts/images/hero.png"
    assert by_type["doc"]["path"] == "reports/brief.pdf"
    assert by_type["app"]["command"] == "npm run dev"
    paths = {a["path"] for a in links}
    assert "artifacts/video/legacy-editable/index.html" not in paths
    assert "artifacts/video/legacy-editable/brief.json" not in paths
    assert "artifacts/video/legacy-editable/render.webm" in paths


def test_dashboard_reuses_artifact_rules_for_legacy_video_shells(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = client.post(
        "/api/projects",
        headers=headers,
        json={"slug": "demo", "name": "Demo"},
    ).json()
    legacy = Path(project["path"]) / "artifacts" / "video" / "old-studio"
    legacy.mkdir(parents=True)
    (legacy / "index.html").write_text("<main>legacy</main>")
    (legacy / "brief.json").write_text("{}")
    (legacy / "render.mp4").write_bytes(b"mp4")

    paths = {
        artifact["path"]
        for artifact in client.get("/api/dashboard", headers=headers).json()["recentArtifacts"]
    }

    assert "artifacts/video/old-studio/index.html" not in paths
    assert "artifacts/video/old-studio/brief.json" not in paths
    assert "artifacts/video/old-studio/render.mp4" in paths
