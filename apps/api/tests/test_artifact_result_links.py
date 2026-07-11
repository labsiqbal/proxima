from __future__ import annotations

import json
import time

from proxima_api.worker import artifacts_for_output_links, scan_project_artifacts


def test_scan_project_artifacts_for_chat_result_links(tmp_path):
    root = tmp_path / "project"
    (root / "artifacts" / "design" / "launch").mkdir(parents=True)
    (root / "artifacts" / "design" / "launch" / "scene.json").write_text(json.dumps({"id": "launch", "title": "Launch Post"}))
    (root / "artifacts" / "video" / "launch-reel").mkdir(parents=True)
    (root / "artifacts" / "video" / "launch-reel" / "index.html").write_text("<title>Launch Reel</title>")
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
    assert by_type["video"]["id"] == "launch-reel"
    assert by_type["video"]["title"] == "Launch Reel"
    assert by_type["video"]["project_slug"] == "alpha"
    assert by_type["image"]["path"] == "artifacts/images/hero.png"
    assert by_type["doc"]["path"] == "reports/brief.pdf"
    assert by_type["app"]["command"] == "npm run dev"
