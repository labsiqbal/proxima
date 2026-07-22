"""Deterministic script nodes end to end (Phase-1 slice 6, T6).

Covers the whole slice contract: dispatch through the runs queue, the
hash-bound trust gate (first-run approval, trusted re-run, changed-bytes
re-approval), the stdin/args I/O hand-off, output-contract validation, and
failure exit codes — all against the real worker + graph advancers, with no
runner/ACP involved anywhere.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from proxima_api import features, scripts_library
from proxima_api.graph_executor import SCRIPT_NODE_RUN_KIND
from proxima_api.main import create_app


def _app(tmp_path, **extra_config):
    config: dict[str, Any] = {
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(tmp_path)],
        "feature_workflow_graph": True,
        "start_worker": False,
        **extra_config,
    }
    return create_app(config)


def _client(app) -> TestClient:
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _project(client: TestClient, tmp_path: Path, slug: str = "proj") -> Path:
    folder = tmp_path / slug
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    res = client.post("/api/projects/link", json={"path": str(folder), "slug": slug})
    assert res.status_code == 201, res.text
    return folder


def _write_script(folder: Path, name: str, body: str) -> Path:
    path = folder / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _script_graph(command: str = "hello.py", **node_extra: Any) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "run", "type": "script", "name": "Run", "command": command, **node_extra}
        ]
    }


def _create_and_start(client: TestClient, graph: dict[str, Any], slug: str | None, job_input: dict | None = None) -> int:
    res = client.post(
        "/api/graph/jobs",
        json={"title": "Plan", "graph": graph, "project_slug": slug, "input": job_input or {}},
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]
    started = client.post(f"/api/graph/jobs/{job_id}/start")
    assert started.status_code == 200, started.text
    return job_id


def _execute_next(app) -> dict[str, Any]:
    run = app.state.worker.claim_run()
    assert run is not None, "no queued run to execute"
    asyncio.run(app.state.worker.execute_run(run))
    return run


def _run_row(app, run_id: int) -> dict[str, Any]:
    return dict(app.state.worker_db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())


def _node(app, job_id: int, node_id: str) -> dict[str, Any]:
    return dict(
        app.state.worker_db.execute(
            "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?", (job_id, node_id)
        ).fetchone()
    )


def _job_status(app, job_id: int) -> str:
    return app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]


def _event_types(app, run_id: int) -> list[str]:
    return [
        r["type"]
        for r in app.state.worker_db.execute(
            "SELECT type FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    ]


def _seed_trust(app, client: TestClient, slug: str, rel_path: str, script_path: Path) -> None:
    project_id = app.state.worker_db.execute(
        "SELECT id FROM projects WHERE slug = ?", (slug,)
    ).fetchone()["id"]
    scripts_library.record_trust(
        app.state.worker_db, project_id, rel_path, scripts_library.content_hash(script_path), 1
    )


HELLO = "# Description: say hello\nprint('hello from script')\n"


# ── dispatch ─────────────────────────────────────────────────────────────


def test_script_node_dispatches_as_a_script_run_not_an_agent_run(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    _write_script(_project(client, tmp_path), "hello.py", HELLO)
    job_id = _create_and_start(client, _script_graph(args=["--fast"]), "proj")

    node = _node(app, job_id, "run")
    assert node["status"] == "running"
    run = _run_row(app, node["run_id"])
    assert run["kind"] == SCRIPT_NODE_RUN_KIND
    assert run["prompt"] == "Run script: scripts/hello.py --fast"
    # The queued-run feature gate must know the new kind.
    assert features.queued_run_feature(run, "chat") == features.WORKFLOW_GRAPH


# ── trust lifecycle ──────────────────────────────────────────────────────


def test_first_run_blocks_for_approval_then_approval_reruns_and_completes(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(folder, "hello.py", HELLO)
    job_id = _create_and_start(client, _script_graph(), "proj")

    # First run: never approved → the step blocks instead of executing.
    first = _execute_next(app)
    node = _node(app, job_id, "run")
    assert node["status"] == "failed"
    assert node["error"].startswith("script_approval_required:")
    assert "scripts/hello.py" in node["error"]
    assert _job_status(app, job_id) == "review"
    assert _run_row(app, first["id"])["status"] == "failed"
    assert "script.approval.required" in _event_types(app, first["id"])

    # The one-time approval binds the current bytes and reruns the step.
    approved = client.post(f"/api/graph/jobs/{job_id}/nodes/run/approve-script")
    assert approved.status_code == 200, approved.text
    trust = app.state.worker_db.execute(
        "SELECT * FROM script_trust WHERE rel_path = 'hello.py'"
    ).fetchone()
    assert trust["content_hash"] == scripts_library.content_hash(script)
    # The approval is visible in the blocked attempt's timeline.
    assert "script.trust.approved" in _event_types(app, first["id"])
    assert _job_status(app, job_id) == "running"

    second = _execute_next(app)
    node = _node(app, job_id, "run")
    assert node["status"] == "done"
    assert json.loads(node["output"]) == "hello from script"
    assert _run_row(app, second["id"])["status"] == "completed"
    assert _job_status(app, job_id) == "review"

    final = client.post(f"/api/graph/jobs/{job_id}/approve")
    assert final.status_code == 200
    assert _job_status(app, job_id) == "done"


def test_trusted_unchanged_script_runs_without_asking_again(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(folder, "hello.py", HELLO)
    _seed_trust(app, client, "proj", "hello.py", script)

    job_id = _create_and_start(client, _script_graph(), "proj")
    _execute_next(app)
    node = _node(app, job_id, "run")
    assert node["status"] == "done"
    assert json.loads(node["output"]) == "hello from script"


def test_changed_bytes_require_reapproval(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(folder, "hello.py", HELLO)
    _seed_trust(app, client, "proj", "hello.py", script)

    # The script's content changes after approval → the binding breaks.
    _write_script(folder, "hello.py", "print('changed!')\n")
    job_id = _create_and_start(client, _script_graph(), "proj")
    _execute_next(app)
    node = _node(app, job_id, "run")
    assert node["status"] == "failed"
    assert node["error"].startswith("script_approval_required:")

    # Re-approval trusts the NEW bytes and the step then runs.
    approved = client.post(f"/api/graph/jobs/{job_id}/nodes/run/approve-script")
    assert approved.status_code == 200, approved.text
    _execute_next(app)
    assert json.loads(_node(app, job_id, "run")["output"]) == "changed!"


def test_approve_script_requires_a_blocked_script_step(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(folder, "hello.py", HELLO)
    _seed_trust(app, client, "proj", "hello.py", script)
    job_id = _create_and_start(client, _script_graph(), "proj")
    _execute_next(app)  # completes; job in final review, node done

    res = client.post(f"/api/graph/jobs/{job_id}/nodes/run/approve-script")
    assert res.status_code == 409
    assert "not blocked on a script approval" in res.text


# ── execution contract ───────────────────────────────────────────────────


def test_script_receives_stdin_handoff_and_substituted_args(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(
        folder,
        "echo.py",
        "# Description: echo the hand-off\n"
        "import json, sys\n"
        "data = json.load(sys.stdin)\n"
        "up = data['upstream'][0]['output'] if data['upstream'] else ''\n"
        "print('brief=' + str(data['job_input'].get('brief', '')) +"
        " ' up=' + str(up) + ' arg=' + sys.argv[1])\n",
    )
    _seed_trust(app, client, "proj", "echo.py", script)
    graph = {
        "nodes": [
            {"id": "collect", "name": "Collect", "instruction": "Collect facts"},
            {
                "id": "run",
                "type": "script",
                "name": "Echo",
                "command": "echo.py",
                "args": ["{{brief}}"],
                "depends_on": ["collect"],
            },
        ]
    }
    job_id = _create_and_start(client, graph, "proj", job_input={"brief": "launch"})

    # The agent node completes with typed output the script must receive.
    agent_run = app.state.worker.claim_run()
    assert agent_run is not None and agent_run["kind"] == "wf_node"
    app.state.worker_db.execute(
        "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (agent_run["id"],),
    )
    app.state.worker.graph_advancers.advance_run(
        agent_run, "verified facts", app.state.worker.add_event
    )

    _execute_next(app)
    node = _node(app, job_id, "run")
    assert node["status"] == "done"
    assert json.loads(node["output"]) == "brief=launch up=verified facts arg=launch"


def test_failing_exit_code_fails_the_node_with_stderr_detail(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(
        folder,
        "boom.py",
        "import sys\nsys.stderr.write('boom detail\\n')\nsys.exit(3)\n",
    )
    _seed_trust(app, client, "proj", "boom.py", script)
    job_id = _create_and_start(client, _script_graph("boom.py"), "proj")
    _execute_next(app)

    node = _node(app, job_id, "run")
    assert node["status"] == "failed"
    assert "exited with code 3" in node["error"]
    assert "boom detail" in node["error"]
    assert _job_status(app, job_id) == "review"


def test_stdout_is_validated_against_the_output_contract(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(folder, "bad-json.py", "print('not json at all')\n")
    _seed_trust(app, client, "proj", "bad-json.py", script)
    job_id = _create_and_start(
        client, _script_graph("bad-json.py", output_kind="json"), "proj"
    )
    _execute_next(app)

    node = _node(app, job_id, "run")
    assert node["status"] == "failed"
    assert "invalid JSON output" in node["error"]


def test_script_timeout_fails_loudly_without_continuation(tmp_path):
    app = _app(tmp_path, run_timeout_seconds=1)
    client = _client(app)
    folder = _project(client, tmp_path)
    script = _write_script(folder, "slow.py", "import time\ntime.sleep(5)\nprint('late')\n")
    _seed_trust(app, client, "proj", "slow.py", script)
    job_id = _create_and_start(client, _script_graph("slow.py"), "proj")
    _execute_next(app)

    node = _node(app, job_id, "run")
    assert node["status"] == "failed"
    assert "timed out after 1s" in node["error"]
    # No continuation chain for deterministic steps: one queued run existed.
    runs = app.state.worker_db.execute(
        "SELECT COUNT(*) AS c FROM runs WHERE kind = ?", (SCRIPT_NODE_RUN_KIND,)
    ).fetchone()["c"]
    assert runs == 1


def test_script_step_without_a_project_fails_loudly(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    job_id = _create_and_start(client, _script_graph(), None)
    _execute_next(app)
    node = _node(app, job_id, "run")
    assert node["status"] == "failed"
    assert "project container" in node["error"]


def test_missing_script_file_fails_the_step(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    _project(client, tmp_path)
    job_id = _create_and_start(client, _script_graph("ghost.py"), "proj")
    _execute_next(app)
    node = _node(app, job_id, "run")
    assert node["status"] == "failed"
    assert "does not exist" in node["error"]
