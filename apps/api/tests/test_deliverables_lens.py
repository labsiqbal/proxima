"""Archive merges into Files as a Deliverables lens (prune Part D, #139).

The deliverable ledger speaks container-relative real paths (decision #122):
records resolve literally against the container root - the same language the
Files tree browses - instead of the retired Ops-relative record dialect.
The lens API adds badge data (latest record per path), a history filter for
records whose file no longer exists, and the approval flow keeps working
through the record surface. Migration v61 rewrites legacy Ops-relative rows
once, idempotently, preserving approvals and lineage.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import artifact_registry, migrations
from proxima_api.main import create_app


def _api(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    app = create_app(
        {
            "database_path": str(tmp_path / "api.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "start_worker": False,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    return api, {"Authorization": f"Bearer {token}"}


def _link(api: TestClient, headers: dict[str, str], root: Path, slug: str):
    roots = api.get("/api/fs/dirs", headers=headers)
    assert roots.status_code == 200, roots.text
    res = api.post(
        "/api/projects/link",
        headers=headers,
        json={
            "path": str(root),
            "root_id": roots.json()["root_id"],
            "name": slug,
            "slug": slug,
        },
    )
    assert res.status_code == 201, res.text
    row = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (slug,)
    ).fetchone()
    return int(row["id"])


def _ops_project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / "ops" / "reports").mkdir(parents=True)
    return root


def _session(conn, project_id: int, job_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, job_id) "
        "VALUES ('chat', ?, 1, ?)",
        (project_id, job_id),
    )
    return int(cur.lastrowid)


def _run(conn, session_id: int, project_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO runs(session_id, project_id, user_id, kind, status, prompt) "
        "VALUES (?, ?, 1, 'chat', 'completed', 'p')",
        (session_id, project_id),
    )
    return int(cur.lastrowid)


def _feed(conn, session_id: int, project_id: int, links: list[dict]) -> int:
    run_id = _run(conn, session_id, project_id)
    artifact_registry.record_run_outputs(conn, run_id, session_id, project_id, links)
    return run_id


# ── record language: container-relative real paths ───────────────────────


def test_records_speak_container_relative_paths(tmp_path: Path):
    api, h = _api(tmp_path)
    root = _ops_project(tmp_path, "proj")
    pid = _link(api, h, root, "proj")
    (root / "ops" / "reports" / "plan.md").write_text("# plan", encoding="utf-8")
    conn = api.app.state.db
    sid = _session(conn, pid)
    _feed(conn, sid, pid, [{"type": "doc", "title": "plan.md", "path": "ops/reports/plan.md"}])

    body = api.get("/api/archive?project=proj", headers=h).json()
    assert body["total"] == 1
    rec = body["items"][0]
    # The path is the same one the Files tree browses (disk is the truth).
    assert rec["path"] == "ops/reports/plan.md"
    assert rec["file_missing"] is False
    assert rec["size"] == len("# plan")
    # The resolved target carries the authoritative Area by physical ownership.
    assert rec["target"]["area"]["kind"] == "ops"
    assert rec["target"]["path"] == "reports/plan.md"


def test_scan_covers_an_outside_ops_artifacts_area(tmp_path: Path):
    # #138's bridge note: an artifacts area detected OUTSIDE the Ops root got
    # files in the right real place but no records until #139. The record scan
    # now follows the layout map across the whole container.
    root = tmp_path / "proj"
    (root / "ops").mkdir(parents=True)
    (root / "artifacts").mkdir()
    api, h = _api(tmp_path)
    _link(api, h, root, "proj")
    (root / "artifacts" / "shot.png").write_bytes(b"\x89PNG")

    res = api.get("/api/projects/proj/artifacts", headers=h)
    assert res.status_code == 200, res.text
    by_path = {a["path"]: a for a in res.json()["artifacts"]}
    assert "artifacts/shot.png" in by_path
    assert by_path["artifacts/shot.png"]["type"] == "image"


def test_scan_keeps_ops_rooted_reports_deliverable(tmp_path: Path):
    # reports/ under the Ops root keeps producing typed deliverables even
    # though the scan is now container-rooted.
    root = _ops_project(tmp_path, "proj")
    api, h = _api(tmp_path)
    _link(api, h, root, "proj")
    (root / "ops" / "reports" / "data.csv").write_text("a,b", encoding="utf-8")

    res = api.get("/api/projects/proj/artifacts", headers=h)
    assert res.status_code == 200, res.text
    by_path = {a["path"]: a for a in res.json()["artifacts"]}
    assert by_path["ops/reports/data.csv"]["type"] == "file"


# ── the history filter: records whose file is gone ───────────────────────


def test_history_filter_lists_gone_file_records(tmp_path: Path):
    api, h = _api(tmp_path)
    root = _ops_project(tmp_path, "proj")
    pid = _link(api, h, root, "proj")
    keep = root / "ops" / "reports" / "keep.md"
    gone = root / "ops" / "reports" / "gone.md"
    keep.write_text("keep", encoding="utf-8")
    gone.write_text("bye", encoding="utf-8")
    conn = api.app.state.db
    sid = _session(conn, pid)
    _feed(conn, sid, pid, [{"type": "doc", "title": "keep.md", "path": "ops/reports/keep.md"}])
    _feed(conn, sid, pid, [{"type": "doc", "title": "gone.md", "path": "ops/reports/gone.md"}])
    gone.unlink()

    # History shows the gone-file record WITHOUT needing a prior full listing
    # to refresh presence first.
    hist = api.get("/api/archive?project=proj&missing=1", headers=h).json()
    assert [r["path"] for r in hist["items"]] == ["ops/reports/gone.md"]
    assert hist["items"][0]["file_missing"] is True
    assert hist["counts"]["missing"] == 1

    present = api.get("/api/archive?project=proj&missing=0", headers=h).json()
    assert [r["path"] for r in present["items"]] == ["ops/reports/keep.md"]

    # The file coming back moves the record out of history.
    gone.write_text("back", encoding="utf-8")
    assert api.get("/api/archive?project=proj&missing=1", headers=h).json()["total"] == 0


# ── badge data: latest record per path ───────────────────────────────────


def test_badges_return_the_latest_record_per_path(tmp_path: Path):
    api, h = _api(tmp_path)
    root = _ops_project(tmp_path, "proj")
    pid = _link(api, h, root, "proj")
    churn = root / "ops" / "reports" / "churn.md"
    churn.write_text("v1", encoding="utf-8")
    other = root / "ops" / "reports" / "other.md"
    other.write_text("o", encoding="utf-8")
    conn = api.app.state.db
    sid = _session(conn, pid)
    _feed(conn, sid, pid, [{"type": "doc", "title": "churn.md", "path": "ops/reports/churn.md"}])
    _feed(conn, sid, pid, [{"type": "doc", "title": "churn.md", "path": "ops/reports/churn.md"}])
    _feed(conn, sid, pid, [{"type": "doc", "title": "other.md", "path": "ops/reports/other.md"}])
    other.unlink()

    res = api.get("/api/archive/badges?project=proj", headers=h)
    assert res.status_code == 200, res.text
    by_path = {b["path"]: b for b in res.json()["items"]}
    assert set(by_path) == {"ops/reports/churn.md", "ops/reports/other.md"}
    # One badge per path: the LATEST version, not every version.
    assert by_path["ops/reports/churn.md"]["version"] == 2
    assert by_path["ops/reports/churn.md"]["status"] == "draft"
    assert by_path["ops/reports/other.md"]["file_missing"] is True
    b = by_path["ops/reports/churn.md"]
    assert {"id", "slug", "path", "status", "version", "file_missing", "type"} <= set(b)


def test_badges_require_a_project(tmp_path: Path):
    api, h = _api(tmp_path)
    assert api.get("/api/archive/badges", headers=h).status_code in (400, 404, 422)
    assert api.get("/api/archive/badges?project=nope", headers=h).status_code == 404


# ── approval flow through the lens surface ───────────────────────────────


def test_approval_flow_stays_intact_through_the_record_surface(tmp_path: Path):
    api, h = _api(tmp_path)
    root = _ops_project(tmp_path, "proj")
    pid = _link(api, h, root, "proj")
    (root / "ops" / "reports" / "out.md").write_text("x", encoding="utf-8")
    conn = api.app.state.db
    sid = _session(conn, pid)
    _feed(conn, sid, pid, [{"type": "doc", "title": "out.md", "path": "ops/reports/out.md"}])
    rec = api.get("/api/archive?project=proj", headers=h).json()["items"][0]

    res = api.post(
        f"/api/archive/records/{rec['id']}/status", headers=h, json={"status": "approved"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    # The record page (the lens' record panel payload) reflects it, with the
    # version chain and lineage intact.
    page = api.get(f"/api/archive/proj/{rec['slug']}", headers=h).json()
    assert page["status"] == "approved"
    assert page["approved_at"]
    assert page["session_id"] == sid
    assert [v["version"] for v in page["versions"]] == [1]
    # And the badge reflects the approval for the tree.
    badge = api.get("/api/archive/badges?project=proj", headers=h).json()["items"][0]
    assert badge["status"] == "approved"


# ── migration v61: legacy Ops-relative rows become container-relative ────


def _legacy_rows(api, pid: int, sid: int) -> tuple[int, int, int]:
    conn = api.app.state.db
    now = "2026-08-01T00:00:00+00:00"
    approved = conn.execute(
        "INSERT INTO artifact_records(project_id, slug, name, type, path, status, version, "
        "session_id, produced_at, approved_at, created_at, updated_at) "
        "VALUES (?, 'plan-md-v1', 'plan.md', 'doc', 'reports/plan.md', 'approved', 1, ?, ?, ?, ?, ?)",
        (pid, sid, now, now, now, now),
    ).lastrowid
    gone = conn.execute(
        "INSERT INTO artifact_records(project_id, slug, name, type, path, status, version, "
        "produced_at, created_at, updated_at, file_missing) "
        "VALUES (?, 'gone-md-v1', 'gone.md', 'doc', 'reports/gone.md', 'draft', 1, ?, ?, ?, 1)",
        (pid, now, now, now),
    ).lastrowid
    literal = conn.execute(
        "INSERT INTO artifact_records(project_id, slug, name, type, path, status, version, "
        "produced_at, created_at, updated_at) "
        "VALUES (?, 'shot-png-v1', 'shot.png', 'image', 'artifacts/shot.png', 'draft', 1, ?, ?, ?)",
        (pid, now, now, now),
    ).lastrowid
    return int(approved), int(gone), int(literal)


def test_v61_reworks_record_paths_idempotently(tmp_path: Path):
    api, h = _api(tmp_path)
    root = _ops_project(tmp_path, "proj")
    pid = _link(api, h, root, "proj")
    (root / "ops" / "reports" / "plan.md").write_text("# plan", encoding="utf-8")
    # A record already speaking container language: its literal file exists
    # OUTSIDE the Ops root (the #138 outside-Ops artifacts case).
    (root / "artifacts").mkdir()
    (root / "artifacts" / "shot.png").write_bytes(b"\x89PNG")
    conn = api.app.state.db
    sid = _session(conn, pid)
    approved_id, gone_id, literal_id = _legacy_rows(api, pid, sid)

    for _ in range(2):  # idempotent: the second run changes nothing
        migrations._rework_artifact_record_paths(conn)
        rows = {
            int(r["id"]): dict(r)
            for r in conn.execute(
                "SELECT * FROM artifact_records WHERE project_id = ?", (pid,)
            ).fetchall()
        }
        # Ops-relative rows gained the Ops prefix; approvals + lineage intact.
        assert rows[approved_id]["path"] == "ops/reports/plan.md"
        assert rows[approved_id]["status"] == "approved"
        assert rows[approved_id]["approved_at"]
        assert rows[approved_id]["session_id"] == sid
        # A gone file freezes its historical Ops meaning.
        assert rows[gone_id]["path"] == "ops/reports/gone.md"
        # An existing literal outside the Ops root is left alone.
        assert rows[literal_id]["path"] == "artifacts/shot.png"


def test_v61_reworks_session_message_and_step_artifacts(tmp_path: Path):
    api, h = _api(tmp_path)
    root = _ops_project(tmp_path, "proj")
    pid = _link(api, h, root, "proj")
    conn = api.app.state.db
    sid = _session(conn, pid)
    arts = [
        {"type": "doc", "title": "plan.md", "path": "reports/plan.md"},
        {"type": "app", "title": "demo", "path": "apps/demo", "dir": "apps/demo"},
    ]
    conn.execute(
        "UPDATE sessions SET produced_artifacts = ? WHERE id = ?",
        (json.dumps(arts), sid),
    )
    conn.execute(
        "INSERT INTO messages(session_id, role, content, output_links) "
        "VALUES (?, 'assistant', 'done', ?)",
        (sid, json.dumps(arts[:1])),
    )
    steps = [{"title": "s1", "status": "done", "produced_artifacts": [dict(arts[0])]}]
    conn.execute(
        "INSERT INTO jobs(project_id, title, status, current_step_idx, steps_state, created_by) "
        "VALUES (?, 'Job', 'review', 0, ?, 1)",
        (pid, json.dumps(steps)),
    )

    for _ in range(2):
        migrations._rework_artifact_record_paths(conn)
        produced = json.loads(
            conn.execute(
                "SELECT produced_artifacts FROM sessions WHERE id = ?", (sid,)
            ).fetchone()[0]
        )
        assert [a["path"] for a in produced] == ["ops/reports/plan.md", "ops/apps/demo"]
        assert produced[1]["dir"] == "ops/apps/demo"
        links = json.loads(
            conn.execute(
                "SELECT output_links FROM messages WHERE session_id = ?", (sid,)
            ).fetchone()[0]
        )
        assert links[0]["path"] == "ops/reports/plan.md"
        job_steps = json.loads(
            conn.execute(
                "SELECT steps_state FROM jobs WHERE project_id = ?", (pid,)
            ).fetchone()[0]
        )
        assert job_steps[0]["produced_artifacts"][0]["path"] == "ops/reports/plan.md"


def test_v61_leaves_dot_ops_projects_alone(tmp_path: Path):
    api, h = _api(tmp_path)
    root = tmp_path / "flat"
    (root / "reports").mkdir(parents=True)
    pid = _link(api, h, root, "flat")
    conn = api.app.state.db
    sid = _session(conn, pid)
    _legacy_rows(api, pid, sid)
    migrations._rework_artifact_record_paths(conn)
    paths = {
        r["path"]
        for r in conn.execute(
            "SELECT path FROM artifact_records WHERE project_id = ?", (pid,)
        ).fetchall()
    }
    assert paths == {"reports/plan.md", "reports/gone.md", "artifacts/shot.png"}
