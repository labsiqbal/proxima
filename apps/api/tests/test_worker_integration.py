"""Integration coverage for the LIVE worker loop + REAL ACP lifecycle.

The rest of the suite runs with ``start_worker=False`` and drives ``execute_run``
directly against an in-process ``FakeAcpManager``. That covers the worker's
internal logic but leaves two things unexercised:

  1. the live ``RunWorker.loop()`` (polling, claim, concurrency, task scheduling),
  2. the REAL ``AcpProcess``/``AcpManager`` lifecycle (subprocess spawn, JSON-RPC
     ``initialize`` handshake, the stdout reader + ``session/update`` dispatch).

This module closes that gap: it points a fake runner spec at a tiny JSON-RPC agent
script, starts the worker for real, enqueues a chat run, and asserts the full
queued -> running -> completed path with streamed events and a persisted message.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import runner_specs
from proxima_api.main import create_app
from proxima_api.runner_specs import RunnerSpec

# Minimal ACP agent: just enough JSON-RPC for one run. Speaks initialize /
# session/new / session/load / session/prompt (emitting one agent_message_chunk
# notification, then end_turn). Run as a REAL subprocess so the AcpProcess
# handshake, reader loop, and session/update dispatch are all exercised.
FAKE_ACP_SCRIPT = '''\
import sys, json
SID = "fake-session-1"
def send(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except Exception:
        continue
    mid = m.get("id"); method = m.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":1,
              "serverInfo":{"name":"fake-acp","version":"0"},"capabilities":{}}})
    elif method == "session/new":
        send({"jsonrpc":"2.0","id":mid,"result":{"sessionId":SID}})
    elif method == "session/load":
        send({"jsonrpc":"2.0","id":mid,"result":{}})
    elif method == "session/prompt":
        send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":SID,
              "update":{"sessionUpdate":"agent_message_chunk",
                        "content":{"type":"text","text":"integration-ok"}}}})
        send({"jsonrpc":"2.0","id":mid,"result":{"stopReason":"end_turn"}})
    else:
        if mid is not None:
            send({"jsonrpc":"2.0","id":mid,"result":{}})
'''


def _install_fake_runner(script: Path) -> None:
    """Register a fake runner whose spawn_argv runs the JSON-RPC script under the
    same interpreter, and make it the default so the auto-provisioned profile
    picks it up."""
    runner_specs.RUNNER_SPECS["fake-acp"] = RunnerSpec(
        id="fake-acp",
        spawn_argv=[sys.executable, str(script)],
        home_env="FAKE_ACP_HOME",
        binary="python",
        display_name="Fake ACP",
        has_adapter=True,
        detection_only=False,
        source_dir="",
        seed_files=(),
        refresh_files=(),
    )
    os.environ["PROXIMA_DEFAULT_RUNNER"] = "fake-acp"


def _wait_for_run(client: TestClient, headers: dict, run_id: int, timeout: float = 15.0) -> str:
    """Poll the run until it reaches a terminal status (the live worker loop runs
    on the TestClient portal loop and advances while this thread sleeps)."""
    deadline = time.time() + timeout
    status = "queued"
    while time.time() < deadline:
        status = client.get(f"/api/runs/{run_id}", headers=headers).json().get("status", status)
        if status in ("completed", "failed", "cancelled"):
            return status
        time.sleep(0.05)
    return status


def test_live_worker_loop_drives_real_acp_subprocess(tmp_path):
    script = tmp_path / "fake_acp.py"
    script.write_text(FAKE_ACP_SCRIPT)
    saved_env = os.environ.get("PROXIMA_DEFAULT_RUNNER")
    saved_spec = runner_specs.RUNNER_SPECS.get("fake-acp")
    _install_fake_runner(script)
    try:
        app = create_app({
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": True,        # the whole point: run the live loop
            "start_scheduler": False,
            "run_worker_poll_interval_ms": 20,  # snappy polling so the test is fast
        })
        with TestClient(app) as client:
            token = client.post("/auth/auto").json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            session = client.post("/api/sessions", headers=headers, json={"title": "integration"}).json()
            run = client.post(
                f"/api/sessions/{session['id']}/runs", headers=headers, json={"message": "go"}
            ).json()

            status = _wait_for_run(client, headers, run["run_id"])
            assert status == "completed", f"run did not complete via live worker: {status}"

            events = client.get(f"/api/sessions/{session['id']}/events", headers=headers).json()["events"]
            types = [e["type"] for e in events]
            assert "run.started" in types
            assert "message.delta" in types          # streamed by the real AcpProcess reader
            assert "message.complete" in types
            assert "run.completed" in types

            msgs = client.get(f"/api/sessions/{session['id']}/messages", headers=headers).json()["messages"]
            assert any("integration-ok" in (m.get("content") or "") for m in msgs), msgs

            # A REAL AcpProcess was spawned + cached: the lifecycle (handshake,
            # reader, dispatch) actually ran, not just an in-process fake.
            assert app.state.acp_manager._procs, "no ACP subprocess was created"
    finally:
        if saved_env is None:
            os.environ.pop("PROXIMA_DEFAULT_RUNNER", None)
        else:
            os.environ["PROXIMA_DEFAULT_RUNNER"] = saved_env
        if saved_spec is None:
            runner_specs.RUNNER_SPECS.pop("fake-acp", None)
        else:
            runner_specs.RUNNER_SPECS["fake-acp"] = saved_spec


# --- cancel-during-setup (locks in the worker race fix) ---------------------

class _CancelDuringSetupProcess:
    """new_session() flips the active run to 'cancelled' (exactly what the cancel
    route does) to simulate a cancel landing DURING setup, before the prompt is
    sent. prompt() must never be called."""

    def __init__(self, mgr):
        self.mgr = mgr

    async def load_session(self, session_id, cwd):
        raise Exception("not loadable")  # force a fresh new_session

    async def new_session(self, cwd):
        with self.mgr.app.state.db_lock:
            row = self.mgr.app.state.worker_db.execute(
                "SELECT id FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row is not None, "expected a running run to cancel during setup"
            self.mgr.app.state.worker_db.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
        return "acp-setup-cancel"

    async def prompt(self, *args, **kwargs):
        self.mgr.prompted = True
        raise AssertionError("prompt must not run when the run was cancelled during setup")

    def cancel(self, session_id):
        pass


class _CancelDuringSetupManager:
    def __init__(self, app):
        self.app = app
        self.prompted = False

    async def get(self, spec=None, home=None, cwd=None):
        return _CancelDuringSetupProcess(self)

    async def recycle(self, spec=None, home=None, cwd=None):
        pass

    async def shutdown(self):
        pass


def test_cancel_during_setup_skips_the_prompt(tmp_path):
    """A cancel that arrives while the agent is being spawned/loaded must
    short-circuit BEFORE the prompt is issued — not after a full agent turn."""
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    mgr = _CancelDuringSetupManager(app)
    app.state.acp_manager = mgr
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    sid = client.post("/api/sessions", headers=headers, json={"title": "c"}).json()["id"]
    client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "hi"})

    async def go():
        run = app.state.worker.claim_run()
        assert run is not None
        await app.state.worker.execute_run(run)

    asyncio.run(go())

    assert mgr.prompted is False, "agent prompt ran despite a cancel during setup"
    row = app.state.worker_db.execute(
        "SELECT status FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (sid,)
    ).fetchone()
    assert row["status"] == "cancelled", f"run status={row['status']}"
