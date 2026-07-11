from __future__ import annotations
from pathlib import Path
from fastapi.testclient import TestClient
from proxima_api.main import create_app


def test_task_crud_and_linked_thread(tmp_path):
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    slug = c.get("/api/projects", headers=h).json()["projects"][0]["slug"]

    t = c.post(f"/api/projects/{slug}/tasks", headers=h, json={"title": "Fix login", "description": "bug"})
    assert t.status_code == 201
    task = t.json()
    assert task["status"] == "todo" and task["session_id"]

    # a dedicated agent thread (session) is linked to the task
    sess = [s for s in c.get("/api/sessions", headers=h).json()["sessions"] if s.get("task_id") == task["id"]]
    assert sess and sess[0]["task_title"] == "Fix login"

    assert len(c.get(f"/api/projects/{slug}/tasks", headers=h).json()["tasks"]) == 1

    # status transitions
    assert c.patch(f"/api/tasks/{task['id']}", headers=h, json={"status": "done"}).json()["status"] == "done"
    assert c.get(f"/api/tasks/{task['id']}", headers=h).json()["status"] == "done"

    # delete removes task + its thread
    assert c.delete(f"/api/tasks/{task['id']}", headers=h).status_code == 200
    assert c.get(f"/api/projects/{slug}/tasks", headers=h).json()["tasks"] == []
    assert [s for s in c.get("/api/sessions", headers=h).json()["sessions"] if s.get("task_id") == task["id"]] == []


def test_run_moves_task_to_review(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})

    class FakeProc:
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}}); return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    slug = c.get("/api/projects", headers=h).json()["projects"][0]["slug"]
    task = c.post(f"/api/projects/{slug}/tasks", headers=h, json={"title": "T"}).json()
    sid = task["session_id"]

    c.post(f"/api/sessions/{sid}/runs", headers=h, json={"message": "go"})
    assert c.get(f"/api/tasks/{task['id']}", headers=h).json()["status"] == "doing"  # set on run create

    async def run_once():
        run = app.state.worker.claim_run(); await app.state.worker.execute_run(run)
    asyncio.run(run_once())
    assert c.get(f"/api/tasks/{task['id']}", headers=h).json()["status"] == "review"  # set on completion


def test_failed_task_run_reverts_task_out_of_doing(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})

    class FakeProc:
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600): raise Exception("boom")
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    slug = c.get("/api/projects", headers=h).json()["projects"][0]["slug"]
    task = c.post(f"/api/projects/{slug}/tasks", headers=h, json={"title": "T"}).json()
    c.post(f"/api/sessions/{task['session_id']}/runs", headers=h, json={"message": "go"})
    assert c.get(f"/api/tasks/{task['id']}", headers=h).json()["status"] == "doing"

    async def run_once():
        run = app.state.worker.claim_run(); await app.state.worker.execute_run(run)
    asyncio.run(run_once())
    # the run failed → the card must not be stranded in 'doing'
    assert c.get(f"/api/tasks/{task['id']}", headers=h).json()["status"] == "todo"


def test_cancelled_task_run_reverts_task_out_of_doing(tmp_path):
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    slug = c.get("/api/projects", headers=h).json()["projects"][0]["slug"]
    task = c.post(f"/api/projects/{slug}/tasks", headers=h, json={"title": "T"}).json()
    rid = c.post(f"/api/sessions/{task['session_id']}/runs", headers=h, json={"message": "go"}).json()["run_id"]
    assert c.get(f"/api/tasks/{task['id']}", headers=h).json()["status"] == "doing"
    c.post(f"/api/runs/{rid}/cancel", headers=h)
    assert c.get(f"/api/tasks/{task['id']}", headers=h).json()["status"] == "todo"


def test_task_payload_includes_creator(tmp_path):
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    slug = c.get("/api/projects", headers=h).json()["projects"][0]["slug"]
    t = c.post(f"/api/projects/{slug}/tasks", headers=h, json={"title": "T"}).json()
    assert t["created_by"] == "owner"


def test_assistant_message_carries_run_id_and_activity(tmp_path):
    # The agent's tool/subagent activity persists on the saved message so the
    # swarm stays visible after the run. 'Task' tools are flagged as subagents.
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})

    class FakeProc:
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            on_update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Task"})
            on_update({"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "Write fib.py"})
            on_update({"sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "completed"})
            on_update({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}})
            return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sid = c.post("/api/sessions", headers=h, json={"title": "t"}).json()["id"]
    c.post(f"/api/sessions/{sid}/runs", headers=h, json={"message": "go"})

    async def run_once():
        r = app.state.worker.claim_run(); assert r; await app.state.worker.execute_run(r)
    asyncio.run(run_once())

    msgs = c.get(f"/api/sessions/{sid}/messages", headers=h).json()["messages"]
    asst = [m for m in msgs if m["role"] == "assistant"][-1]
    assert asst["run_id"]
    titles = {a["title"]: a for a in asst.get("activity", [])}
    assert "Task" in titles and titles["Task"]["subagent"] is True      # subagent flagged
    assert "Write fib.py" in titles and titles["Write fib.py"]["subagent"] is False
    assert titles["Task"]["status"] == "completed"


def test_successful_run_appends_auto_log(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})

    class FakeProc:
        def __init__(self): self.calls = 0
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            self.calls += 1
            if self.calls == 1:
                on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Built the feature."}})
            else:  # the summarize follow-up
                on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Implemented login and added tests."}})
            return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        _proc = FakeProc()
        async def get(self, spec=None, home=None, cwd=None): return FakeMgr._proc
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]
    c.post(f"/api/sessions/{sid}/runs", headers=h, json={"message": "build login"})

    async def run_once():
        r = app.state.worker.claim_run(); assert r; await app.state.worker.execute_run(r)
    asyncio.run(run_once())

    log = Path(proj["path"]) / "wiki" / "log.md"
    assert log.exists()
    assert "Implemented login and added tests." in log.read_text(encoding="utf-8")
    assert FakeMgr._proc.calls == 3   # main turn + auto-title turn + summarize turn


def test_auto_log_failure_does_not_fail_run(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})

    class FakeProc:
        def __init__(self): self.calls = 0
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            self.calls += 1
            if self.calls == 1:
                on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Did the work."}})
                return "end_turn"
            raise RuntimeError("summarizer exploded")
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]
    c.post(f"/api/sessions/{sid}/runs", headers=h, json={"message": "go"})

    async def run_once():
        r = app.state.worker.claim_run(); await app.state.worker.execute_run(r)
    asyncio.run(run_once())

    msgs = c.get(f"/api/sessions/{sid}/messages", headers=h).json()["messages"]
    asst = [m for m in msgs if m["role"] == "assistant"][-1]
    assert asst["content"] == "Did the work."   # run still completed cleanly


def test_wiki_draft_run_emits_draft_event(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})

    class FakeProc:
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            draft = '```json\n{"title":"Caching","path":"perf/caching.md","body":"# Caching","related":[],"conflicts":[],"action":"new","target":null}\n```'
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": draft}})
            return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]
    r = c.post(f"/api/sessions/{sid}/wiki-note/draft", headers=h, json={"profile_id": None})
    assert r.status_code == 202

    async def run_once():
        run = app.state.worker.claim_run(); assert run["kind"] == "wiki_draft"; await app.state.worker.execute_run(run)
    asyncio.run(run_once())

    events = c.get(f"/api/sessions/{sid}/events", headers=h).json()["events"]
    drafts = [e for e in events if e["type"] == "wiki.draft"]
    assert drafts, "expected a wiki.draft event"
    payload = drafts[-1]["payload"]
    assert payload["title"] == "Caching"
    assert payload["path"] == "perf/caching.md"
    msgs = c.get(f"/api/sessions/{sid}/messages", headers=h).json()["messages"]
    assert not [m for m in msgs if m["role"] == "assistant"]


def test_wiki_note_commit_writes_and_merges(tmp_path):
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]
    note = Path(proj["path"]) / "wiki" / "perf" / "caching.md"

    r = c.post(f"/api/sessions/{sid}/wiki-note/commit", headers=h,
               json={"path": "perf/caching.md", "content": "# Caching\nUse Redis.", "mode": "new"})
    assert r.status_code == 200
    assert "Use Redis." in note.read_text(encoding="utf-8")

    r2 = c.post(f"/api/sessions/{sid}/wiki-note/commit", headers=h,
                json={"path": "perf/caching.md", "content": "## Update\nAdd TTL.", "mode": "append"})
    assert r2.status_code == 200
    merged = note.read_text(encoding="utf-8")
    assert "Use Redis." in merged and "Add TTL." in merged


def test_projectless_chat_has_no_wiki(tmp_path):
    # Wiki is project-scoped: a chat without a project can't be saved to a wiki,
    # and produces no auto-log (no hidden personal wiki).
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})

    class FakeProc:
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Did it."}})
            return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sid = c.post("/api/sessions", headers=h, json={"title": "t"}).json()["id"]   # no project_slug

    # Draft is rejected for a project-less chat.
    r = c.post(f"/api/sessions/{sid}/wiki-note/draft", headers=h, json={"profile_id": None})
    assert r.status_code == 400

    # A normal run completes but writes no log anywhere under the workspace.
    c.post(f"/api/sessions/{sid}/runs", headers=h, json={"message": "go"})

    async def run_once():
        run = app.state.worker.claim_run(); await app.state.worker.execute_run(run)
    asyncio.run(run_once())

    assert not list((tmp_path / "ws").rglob("log.md"))


def test_wiki_commit_rebuilds_index(tmp_path):
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]

    r = c.post(f"/api/sessions/{sid}/wiki-note/commit", headers=h,
               json={"path": "perf/caching.md", "content": "# Caching\n\nUse Redis.", "mode": "new"})
    assert r.status_code == 200
    idx = (Path(proj["path"]) / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[Caching](perf/caching.md)" in idx
    assert "Use Redis." in idx


def test_fresh_session_prompt_gets_proxima_preamble(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    seen = {}

    class FakeProc:
        async def load_session(self, *a): raise Exception("new")   # force fresh new_session
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            seen.setdefault("prompts", []).append(text)
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}})
            return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]
    c.post(f"/api/sessions/{sid}/runs", headers=h, json={"message": "hello there"})

    async def run_once():
        run = app.state.worker.claim_run(); await app.state.worker.execute_run(run)
    asyncio.run(run_once())

    sent = seen["prompts"][0]
    assert sent.startswith("[Proxima context]")
    assert "hello there" in sent          # the real user message still follows


def test_resumed_session_prompt_has_no_preamble(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    seen = {"prompts": []}

    class FakeProc:
        async def load_session(self, sid, cwd): pass        # session already exists -> resume
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            seen["prompts"].append(text)
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}})
            return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        _p = FakeProc()
        async def get(self, spec=None, home=None, cwd=None): return FakeMgr._p
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]
    # seed the agent_sessions row so this home already has an ACP session -> load path
    # use the actual hermes_home from the profile so the lookup matches
    profiles = c.get("/api/profiles", headers=h).json()["profiles"]
    hermes_home = profiles[0]["hermes_home"]
    app.state.worker_db.execute(
        "INSERT OR REPLACE INTO agent_sessions(session_id, hermes_home, acp_session_id) VALUES (?, ?, ?)",
        (sid, hermes_home, "acp-1"))
    app.state.worker_db.commit()

    c.post(f"/api/sessions/{sid}/runs", headers=h, json={"message": "second turn"})

    async def run_once():
        run = app.state.worker.claim_run(); await app.state.worker.execute_run(run)
    asyncio.run(run_once())

    sent = seen["prompts"][0]
    assert "[Proxima context]" not in sent
    assert sent == "second turn"


def test_wiki_draft_run_has_no_preamble(tmp_path):
    import asyncio
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    seen = {"prompts": []}

    class FakeProc:
        async def load_session(self, *a): raise Exception("new")
        async def new_session(self, *a): return "acp-1"
        async def prompt(self, sid, text, on_update, on_permission=None, timeout=600):
            seen["prompts"].append(text)
            draft = '```json\n{"title":"X","path":"a.md","body":"# X","related":[],"conflicts":[],"action":"new","target":null}\n```'
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": draft}})
            return "end_turn"
        def cancel(self, *a): pass

    class FakeMgr:
        async def get(self, spec=None, home=None, cwd=None): return FakeProc()
        async def recycle(self, *a, **k): pass
        async def shutdown(self): pass

    app.state.acp_manager = FakeMgr()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.get("/api/projects", headers=h).json()["projects"][0]
    sid = c.post("/api/sessions", headers=h, json={"title": "t", "project_slug": proj["slug"]}).json()["id"]
    c.post(f"/api/sessions/{sid}/wiki-note/draft", headers=h, json={"profile_id": None})

    async def run_once():
        run = app.state.worker.claim_run(); await app.state.worker.execute_run(run)
    asyncio.run(run_once())

    assert "[Proxima context]" not in seen["prompts"][0]
