"""Unit tests for the native Codex app-server driver's wire translation.

These cover the pure mapping logic (app-server events/errors/approvals -> the
ACP-style shapes the worker consumes) without spawning a process. The live
end-to-end proof that the driver actually runs `gpt-5.6-sol` against the ChatGPT
backend is exercised manually against the system Codex CLI (see the PR body):
that path needs real OAuth + network, so it is not a hermetic unit test.
"""
from proxima_api.codex_appserver import (
    CodexAppServerProcess,
    _approval_decisions,
    _approval_title,
    _tool_title,
)
from proxima_api.runner_specs import RUNNER_SPECS


def _proc():
    p = CodexAppServerProcess(RUNNER_SPECS["codex"], "/tmp/home", "/tmp/cwd")
    p._codex_path = "/usr/bin/codex"
    return p


def test_agent_message_delta_maps_to_agent_message_chunk():
    p = _proc()
    seen = []
    p._handlers["t1"] = seen.append
    p._handle_notification("item/agentMessage/delta", {"threadId": "t1", "delta": "OK"})
    assert seen == [{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OK"}}]


def test_reasoning_delta_maps_to_thought_chunk():
    p = _proc()
    seen = []
    p._handlers["t1"] = seen.append
    p._handle_notification("item/reasoning/textDelta", {"threadId": "t1", "delta": "thinking"})
    p._handle_notification("item/reasoning/summaryTextDelta", {"threadId": "t1", "delta": "summary"})
    assert [u["sessionUpdate"] for u in seen] == ["agent_thought_chunk", "agent_thought_chunk"]
    assert [u["content"]["text"] for u in seen] == ["thinking", "summary"]


def test_tool_item_start_and_complete_map_to_tool_events():
    p = _proc()
    seen = []
    p._handlers["t1"] = seen.append
    p._handle_notification("item/started", {"threadId": "t1", "item": {"type": "commandExecution", "id": "c1", "command": ["echo", "hi"]}})
    p._handle_notification("item/completed", {"threadId": "t1", "item": {"type": "commandExecution", "id": "c1"}})
    assert seen[0]["sessionUpdate"] == "tool_call"
    assert seen[0]["toolCallId"] == "c1"
    assert seen[1] == {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed"}


def test_failed_tool_item_reports_failed_status():
    p = _proc()
    seen = []
    p._handlers["t1"] = seen.append
    p._handle_notification("item/completed", {"threadId": "t1", "item": {"type": "commandExecution", "id": "c1", "error": "boom"}})
    assert seen[0]["status"] == "failed"


def test_non_tool_items_are_ignored():
    # agentMessage item/completed must NOT emit a tool event (deltas already
    # streamed the text; re-emitting would duplicate output).
    p = _proc()
    seen = []
    p._handlers["t1"] = seen.append
    p._handle_notification("item/completed", {"threadId": "t1", "item": {"type": "agentMessage", "id": "m1", "text": "OK"}})
    assert seen == []


def test_turn_completed_resolves_turn_future():
    import asyncio

    async def go():
        p = _proc()
        fut = asyncio.get_event_loop().create_future()
        p._turn_done["t1"] = fut
        p._handle_notification("turn/completed", {"threadId": "t1", "turn": {"status": "completed", "error": None}})
        return await fut

    assert asyncio.run(go()) == ("completed", None)


def test_version_gate_error_is_de_misled():
    p = _proc()
    err = {"message": '{"type":"error","status":400,"error":{"message":"The \'gpt-5.6-sol\' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again."}}'}
    out = p._explain_turn_error(err)
    assert "requires a newer version of Codex" in out
    # honest, actionable guidance that points at the actual system binary
    assert "system Codex CLI" in out
    assert "/usr/bin/codex" in out
    assert "codex update" in out


def test_ordinary_backend_error_passes_through():
    p = _proc()
    out = p._explain_turn_error({"message": '{"error":{"message":"rate limit exceeded"}}'})
    assert out == "rate limit exceeded"


def test_approval_decision_vocabulary():
    assert _approval_decisions("execCommandApproval") == {
        "allow_once": "approved", "allow_always": "approved_for_session", "reject": "denied"}
    assert _approval_decisions("applyPatchApproval")["allow_once"] == "approved"
    assert _approval_decisions("item/commandExecution/requestApproval") == {
        "allow_once": "accept", "allow_always": "acceptForSession", "reject": "decline"}
    assert _approval_decisions("item/fileChange/requestApproval")["reject"] == "decline"
    # requests we don't answer with a plain {decision} reply
    assert _approval_decisions("item/permissions/requestApproval") is None


def test_approval_title_prefers_command_string():
    assert _approval_title({"command": "/bin/zsh -lc 'mkdir x'"}) == "/bin/zsh -lc 'mkdir x'"
    assert _approval_title({"command": ["echo", "hi"]}) == "echo hi"
    assert _approval_title({"reason": "network access"}) == "network access"


def test_tool_title_renders_command_and_types():
    assert _tool_title({"type": "commandExecution", "command": ["ls", "-la"]}) == "ls -la"
    assert _tool_title({"type": "fileChange"}) == "edit files"
    assert _tool_title({"type": "mcpToolCall", "toolName": "search"}) == "search"


def test_resolve_permission_delivers_choice():
    import asyncio

    async def go():
        p = _proc()
        fut = asyncio.get_event_loop().create_future()
        p._perm_futures["r1"] = fut
        assert p.resolve_permission("r1", "accept") is True
        assert p.resolve_permission("missing", "accept") is False
        return await fut

    assert asyncio.run(go()) == "accept"
