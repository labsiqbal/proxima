"""Durable deliverable registry (Phase-1 slice 8, T4): feed, versioning,
one-status-two-doors approval sync, permalinks, filters/pagination, seed."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import artifact_registry
from proxima_api.main import create_app
from proxima_api.migrations import _add_artifact_registry


def _app(tmp_path: Path):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "start_worker": False,
        }
    )


def _client(app) -> tuple[TestClient, dict[str, str]]:
    api = TestClient(app)
    res = api.post("/auth/auto")
    assert res.status_code == 200
    return api, {"Authorization": f"Bearer {res.json()['token']}"}


def _project(api: TestClient, h: dict[str, str], root: Path, slug: str) -> dict:
    (root / "reports").mkdir(parents=True, exist_ok=True)
    res = api.post("/api/projects/link", headers=h, json={"path": str(root), "slug": slug})
    assert res.status_code == 201, res.text
    payload = res.json()
    row = api.app.state.db.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    return {**payload, "id": int(row["id"])}


def _session(conn, project_id: int, job_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, job_id) VALUES ('chat', ?, 1, ?)",
        (project_id, job_id),
    )
    return int(cur.lastrowid)


def _run(conn, session_id: int, project_id: int, kind: str = "chat") -> int:
    cur = conn.execute(
        "INSERT INTO runs(session_id, project_id, user_id, kind, status, prompt) "
        "VALUES (?, ?, 1, ?, 'completed', 'p')",
        (session_id, project_id, kind),
    )
    return int(cur.lastrowid)


def _feed(conn, run_id: int, session_id: int, project_id: int, links: list[dict]) -> None:
    artifact_registry.record_run_outputs(conn, run_id, session_id, project_id, links)


def _records(conn, project_id: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM artifact_records WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
    ]


# ── feed: records, idempotence, script-output typing ─────────────────────


def test_feed_creates_draft_records_with_lineage(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    (root / "reports" / "plan.md").write_text("# plan", encoding="utf-8")
    conn = app.state.db
    sid = _session(conn, p["id"])
    rid = _run(conn, sid, p["id"])
    _feed(conn, rid, sid, p["id"], [{"type": "doc", "title": "plan.md", "path": "reports/plan.md"}])
    recs = _records(conn, p["id"])
    assert len(recs) == 1
    r = recs[0]
    assert r["status"] == "draft"
    assert r["version"] == 1
    assert r["session_id"] == sid and r["run_id"] == rid
    assert r["size"] == len("# plan")
    assert r["slug"].startswith("plan-md-v1")


def test_feed_is_idempotent_for_the_same_run(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    (root / "reports" / "plan.md").write_text("# plan", encoding="utf-8")
    conn = app.state.db
    sid = _session(conn, p["id"])
    rid = _run(conn, sid, p["id"])
    links = [{"type": "doc", "title": "plan.md", "path": "reports/plan.md"}]
    _feed(conn, rid, sid, p["id"], links)
    _feed(conn, rid, sid, p["id"], links)  # per-step + final scan of one run
    recs = _records(conn, p["id"])
    assert len(recs) == 1
    assert recs[0]["version"] == 1


def test_script_run_files_become_script_output_records(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    (root / "reports" / "sync.log").write_text("ok", encoding="utf-8")
    (root / "reports" / "summary.md").write_text("# s", encoding="utf-8")
    conn = app.state.db
    sid = _session(conn, p["id"])
    rid = _run(conn, sid, p["id"], kind="wf_script_node")
    _feed(
        conn,
        rid,
        sid,
        p["id"],
        [
            {"type": "file", "title": "sync.log", "path": "reports/sync.log"},
            {"type": "doc", "title": "summary.md", "path": "reports/summary.md"},
        ],
    )
    by_path = {r["path"]: r for r in _records(conn, p["id"])}
    assert by_path["reports/sync.log"]["type"] == "script-output"
    assert by_path["reports/summary.md"]["type"] == "doc"  # richer types keep their identity


# ── version chain ─────────────────────────────────────────────────────────


def test_new_producer_at_same_path_creates_next_version_and_supersedes(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    (root / "reports" / "churn.md").write_text("june", encoding="utf-8")
    conn = app.state.db
    s1 = _session(conn, p["id"])
    r1 = _run(conn, s1, p["id"])
    _feed(conn, r1, s1, p["id"], [{"type": "doc", "title": "churn.md", "path": "reports/churn.md"}])
    (root / "reports" / "churn.md").write_text("july update", encoding="utf-8")
    s2 = _session(conn, p["id"])
    r2 = _run(conn, s2, p["id"])
    _feed(conn, r2, s2, p["id"], [{"type": "doc", "title": "churn.md", "path": "reports/churn.md"}])
    recs = _records(conn, p["id"])
    assert [r["version"] for r in recs] == [1, 2]
    v1, v2 = recs
    assert v1["status"] == "superseded"
    assert v1["superseded_by"] == v2["id"]
    assert v2["status"] == "draft"
    assert v2["slug"] != v1["slug"]  # each version keeps its own permanent address


# ── approval: one status, two doors ──────────────────────────────────────


def test_job_approve_auto_approves_its_records(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    (root / "reports" / "out.md").write_text("done", encoding="utf-8")
    conn = app.state.db
    cur = conn.execute(
        "INSERT INTO jobs(project_id, title, status, current_step_idx, steps_state, created_by) "
        "VALUES (?, 'Job', 'review', 0, '[]', 1)",
        (p["id"],),
    )
    job_id = int(cur.lastrowid)
    sid = _session(conn, p["id"], job_id=job_id)
    conn.execute("UPDATE jobs SET session_id = ? WHERE id = ?", (sid, job_id))
    rid = _run(conn, sid, p["id"])
    _feed(conn, rid, sid, p["id"], [{"type": "doc", "title": "out.md", "path": "reports/out.md"}])
    rec = _records(conn, p["id"])[0]
    assert rec["job_id"] == job_id and rec["status"] == "draft"
    res = api.post(f"/api/jobs/{job_id}/approve", headers=h)
    assert res.status_code == 200, res.text
    rec = _records(conn, p["id"])[0]
    assert rec["status"] == "approved"
    assert rec["approved_at"]


def test_archive_door_edits_the_same_status(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    (root / "reports" / "out.md").write_text("x", encoding="utf-8")
    conn = app.state.db
    sid = _session(conn, p["id"])
    rid = _run(conn, sid, p["id"])
    _feed(conn, rid, sid, p["id"], [{"type": "doc", "title": "out.md", "path": "reports/out.md"}])
    rec_id = _records(conn, p["id"])[0]["id"]
    res = api.post(f"/api/archive/records/{rec_id}/status", headers=h, json={"status": "approved"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    assert _records(conn, p["id"])[0]["status"] == "approved"
    res = api.post(f"/api/archive/records/{rec_id}/status", headers=h, json={"status": "nope"})
    assert res.status_code == 422


# ── registry queries: list, filters, pagination, permalink ──────────────


def _seed_many(api, h, app, tmp_path: Path, n: int = 5):
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    conn = app.state.db
    sid = _session(conn, p["id"])
    for i in range(n):
        (root / "reports" / f"r{i}.md").write_text(f"r{i}", encoding="utf-8")
        rid = _run(conn, sid, p["id"])
        _feed(conn, rid, sid, p["id"], [{"type": "doc", "title": f"r{i}.md", "path": f"reports/r{i}.md"}])
    return p, conn, sid


def test_list_paginates_newest_first_with_counts(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    p, conn, sid = _seed_many(api, h, app, tmp_path, n=5)
    res = api.get("/api/archive?limit=2&offset=0", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["counts"]["by_type"] == {"doc": 5}
    assert body["counts"]["by_status"] == {"draft": 5}
    # No item cap: the rest is reachable page by page.
    rest = api.get("/api/archive?limit=200&offset=2", headers=h).json()
    assert len(rest["items"]) == 3
    ids = [i["id"] for i in body["items"]] + [i["id"] for i in rest["items"]]
    assert len(set(ids)) == 5


def test_list_filters_by_type_status_query_and_project(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    p, conn, sid = _seed_many(api, h, app, tmp_path, n=3)
    (tmp_path / "proj" / "reports" / "shot.png").write_bytes(b"\x89PNG")
    rid = _run(conn, sid, p["id"])
    _feed(conn, rid, sid, p["id"], [{"type": "image", "title": "shot.png", "path": "reports/shot.png"}])
    assert api.get("/api/archive?type=image", headers=h).json()["total"] == 1
    assert api.get("/api/archive?status=draft", headers=h).json()["total"] == 4
    assert api.get("/api/archive?q=r1", headers=h).json()["total"] == 1
    assert api.get("/api/archive?project=proj", headers=h).json()["total"] == 4
    assert api.get("/api/archive?project=other", headers=h).json()["total"] == 0
    # Facet counts ignore the type/status filters (chips stay stable).
    counts = api.get("/api/archive?type=image", headers=h).json()["counts"]
    assert counts["by_type"] == {"doc": 3, "image": 1}


def test_permalink_record_page_payload(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    p, conn, sid = _seed_many(api, h, app, tmp_path, n=3)
    items = api.get("/api/archive", headers=h).json()["items"]
    middle = items[1]
    res = api.get(f"/api/archive/proj/{middle['slug']}", headers=h)
    assert res.status_code == 200, res.text
    rec = res.json()
    assert rec["project_slug"] == "proj"
    assert rec["session_title"] == "chat"
    assert [v["version"] for v in rec["versions"]] == [1]
    assert rec["prev_slug"] == items[0]["slug"]
    assert rec["next_slug"] == items[2]["slug"]
    assert api.get("/api/archive/proj/no-such-record", headers=h).status_code == 404


def test_version_history_on_record_page(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    conn = app.state.db
    sid = _session(conn, p["id"])
    (root / "reports" / "churn.md").write_text("v1", encoding="utf-8")
    _feed(conn, _run(conn, sid, p["id"]), sid, p["id"], [{"type": "doc", "title": "churn.md", "path": "reports/churn.md"}])
    _feed(conn, _run(conn, sid, p["id"]), sid, p["id"], [{"type": "doc", "title": "churn.md", "path": "reports/churn.md"}])
    v1, v2 = _records(conn, p["id"])
    rec = api.get(f"/api/archive/proj/{v1['slug']}", headers=h).json()
    assert [v["version"] for v in rec["versions"]] == [2, 1]
    assert rec["superseded_by_slug"] == v2["slug"]


# ── durable records: file moves/deletion ─────────────────────────────────


def test_record_survives_file_deletion_and_notes_it(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    f = root / "reports" / "gone.md"
    f.write_text("bye", encoding="utf-8")
    conn = app.state.db
    sid = _session(conn, p["id"])
    _feed(conn, _run(conn, sid, p["id"]), sid, p["id"], [{"type": "doc", "title": "gone.md", "path": "reports/gone.md"}])
    f.unlink()
    body = api.get("/api/archive", headers=h).json()
    assert body["total"] == 1
    assert body["items"][0]["file_missing"] is True
    # The file coming back flips it again.
    f.write_text("back", encoding="utf-8")
    assert api.get("/api/archive", headers=h).json()["items"][0]["file_missing"] is False


# ── migration seed ────────────────────────────────────────────────────────


def test_seed_registers_existing_artifacts_as_draft_records(tmp_path: Path):
    app = _app(tmp_path)
    api, h = _client(app)
    root = tmp_path / "proj"
    p = _project(api, h, root, "proj")
    (root / "reports" / "old.md").write_text("existing", encoding="utf-8")
    (root / "artifacts").mkdir()
    (root / "artifacts" / "page.html").write_text("<html></html>", encoding="utf-8")
    conn = app.state.db
    inserted = artifact_registry.seed_project(conn, p["id"], root)
    assert inserted == 2
    recs = _records(conn, p["id"])
    assert {r["status"] for r in recs} == {"draft"}
    assert all(r["version"] == 1 for r in recs)
    # Re-running the seed (or the migration fn) is harmless.
    assert artifact_registry.seed_project(conn, p["id"], root) == 0
    _add_artifact_registry(conn)
    assert len(_records(conn, p["id"])) == 2
