"""Unit tests for the native Codex app-server driver's wire translation.

These cover the pure mapping logic (app-server events/errors/approvals -> the
ACP-style shapes the worker consumes) without spawning a process. The live
end-to-end proof that the driver actually runs `gpt-5.6-sol` against the ChatGPT
backend is exercised manually against the system Codex CLI (see the PR body):
that path needs real OAuth + network, so it is not a hermetic unit test.
"""
import asyncio
from types import SimpleNamespace

import pytest

from proxima_api.codex_appserver import (
    MASTER_APP_SERVER_CONFIG,
    MASTER_CODEX_BASE_INSTRUCTIONS,
    CodexAppServerProcess,
    _approval_decisions,
    _approval_title,
    _tool_title,
)
from proxima_api.codex_master_proxy import (
    CodexMasterModelProxy,
    MasterModelRequestError,
    reconstruct_developer_context,
    reconstruct_model_tools,
)
from proxima_api.master_tool_broker import master_dynamic_tools
from proxima_api.runner_specs import RUNNER_SPECS


def _proc(*, master_chat_only=False):
    p = CodexAppServerProcess(
        RUNNER_SPECS["codex"],
        "/tmp/home",
        "/tmp/cwd",
        master_chat_only=master_chat_only,
    )
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
    async def go():
        p = _proc(master_chat_only=True)
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


def test_master_errors_redact_paths_and_credential_like_text():
    p = _proc(master_chat_only=True)
    out = p._explain_turn_error(
        {
            "message": (
                "failed at /host/runtime/config.toml with "
                "Bearer provider-secret-material"
            )
        }
    )
    assert "/host/runtime/config.toml" not in out
    assert "provider-secret-material" not in out
    assert "[protected-path]" in out


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
    async def go():
        p = _proc()
        fut = asyncio.get_event_loop().create_future()
        p._perm_futures["r1"] = fut
        assert p.resolve_permission("r1", "accept") is True
        assert p.resolve_permission("missing", "accept") is False
        return await fut

    assert asyncio.run(go()) == "accept"


def test_master_thread_has_no_execution_environment_or_inherited_capabilities():
    async def go():
        p = _proc(master_chat_only=True)
        p._master_proxy = SimpleNamespace(
            set_product_tools=lambda tools, required_names: None
        )
        requests = []

        async def request(method, params):
            requests.append((method, params))
            return {"thread": {"id": "master-thread"}}

        p._request = request
        tools = master_dynamic_tools()
        thread_id = await p.new_master_session("/master", tools)
        return thread_id, requests

    thread_id, requests = asyncio.run(go())
    assert thread_id == "master-thread"
    method, params = requests[0]
    assert method == "thread/start"
    assert params["approvalPolicy"] == "never"
    assert params["sandbox"] == "read-only"
    assert params["baseInstructions"] == MASTER_CODEX_BASE_INSTRUCTIONS
    assert "supplied Proxima product functions" in params["developerInstructions"]
    assert params["environments"] == []
    assert params["runtimeWorkspaceRoots"] == []
    assert params["selectedCapabilityRoots"] == []
    assert {tool["name"] for tool in params["dynamicTools"]} == {
        tool["name"] for tool in master_dynamic_tools()
    }
    assert params["ephemeral"] is True


def test_master_strict_config_disables_detected_capability_sources():
    assert 'approval_policy="never"' in MASTER_APP_SERVER_CONFIG
    assert 'sandbox_mode="read-only"' in MASTER_APP_SERVER_CONFIG
    assert 'web_search="disabled"' in MASTER_APP_SERVER_CONFIG
    assert "features.shell_tool=false" in MASTER_APP_SERVER_CONFIG
    assert "features.in_app_browser=false" in MASTER_APP_SERVER_CONFIG
    assert "features.browser_use=false" in MASTER_APP_SERVER_CONFIG
    assert "features.image_generation=false" in MASTER_APP_SERVER_CONFIG
    assert "features.enable_request_compression=false" in MASTER_APP_SERVER_CONFIG
    assert "features.apps=false" in MASTER_APP_SERVER_CONFIG
    assert "features.plugins=false" in MASTER_APP_SERVER_CONFIG
    assert "features.hooks=false" in MASTER_APP_SERVER_CONFIG
    assert "skills.bundled.enabled=false" in MASTER_APP_SERVER_CONFIG
    assert (
        "tools.experimental_request_user_input.enabled=false"
        in MASTER_APP_SERVER_CONFIG
    )
    assert "orchestrator.skills.enabled=false" in MASTER_APP_SERVER_CONFIG
    assert "orchestrator.mcp.enabled=false" in MASTER_APP_SERVER_CONFIG
    assert "include_environment_context=false" in MASTER_APP_SERVER_CONFIG
    assert "project_doc_max_bytes=0" in MASTER_APP_SERVER_CONFIG


def test_master_model_firewall_keeps_only_exact_dynamic_product_tools():
    product_tools = (
        {
            "type": "function",
            "name": "list_tasks",
            "description": "List tasks",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "delegate_tasks",
            "description": "Delegate tasks",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
            },
        },
    )
    payload = {
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {"type": "function", "name": "update_plan"},
                    {
                        "type": "function",
                        "name": "list_tasks",
                        "description": "List tasks",
                        "strict": False,
                        "parameters": product_tools[0]["inputSchema"],
                    },
                    {
                        "type": "function",
                        "name": "delegate_tasks",
                        "description": "Delegate tasks",
                        "strict": False,
                        "parameters": product_tools[1]["inputSchema"],
                    },
                ],
            }
        ],
        "tool_choice": "auto",
    }
    restricted, seen, removed = reconstruct_model_tools(payload, product_tools)
    tools = restricted["input"][0]["tools"]
    assert [tool["name"] for tool in tools] == [
        "list_tasks",
        "delegate_tasks",
    ]
    assert tools[0]["parameters"] == product_tools[0]["inputSchema"]
    assert tools[0]["strict"] is False
    assert seen == {"list_tasks", "delegate_tasks"}
    assert removed == {"update_plan"}

    with pytest.raises(
        MasterModelRequestError,
        match="unrecognized runner-native tools",
    ):
        reconstruct_model_tools(
            {
                "tools": [
                    {
                        "type": "function",
                        "name": "list_tasks",
                        "description": "List tasks",
                        "strict": False,
                        "parameters": product_tools[0]["inputSchema"],
                    },
                    {
                        "type": "function",
                        "name": "delegate_tasks",
                        "description": "Delegate tasks",
                        "strict": False,
                        "parameters": product_tools[1]["inputSchema"],
                    },
                    {"type": "function", "name": "browser"},
                ]
            },
            product_tools,
        )


def test_master_model_firewall_rejects_missing_or_duplicate_product_tools():
    import pytest

    product_tools = (
        {
            "type": "function",
            "name": "list_tasks",
            "description": "List tasks",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "delegate_tasks",
            "description": "Delegate tasks",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
            },
        },
    )
    with pytest.raises(MasterModelRequestError, match="omitted"):
        reconstruct_model_tools({"input": []}, product_tools)
    reconstructed, names, removed = reconstruct_model_tools(
        {"input": []},
        product_tools,
        allow_attested_omission=True,
    )
    assert names == {"list_tasks", "delegate_tasks"}
    assert removed == set()
    assert reconstructed["input"][0]["type"] == "additional_tools"
    with pytest.raises(MasterModelRequestError, match="incomplete"):
        reconstruct_model_tools(
            {
                "tools": [
                    {
                        "type": "function",
                        "name": "list_tasks",
                        "description": "List tasks",
                        "strict": False,
                        "parameters": product_tools[0]["inputSchema"],
                    }
                ]
            },
            product_tools,
        )
    with pytest.raises(MasterModelRequestError, match="duplicates"):
        reconstruct_model_tools(
            {
                "tools": [
                    {"type": "function", "name": "list_tasks"},
                    {"type": "function", "name": "list_tasks"},
                ]
            },
            product_tools,
        )


def test_master_model_firewall_rejects_changed_product_schema():
    import pytest

    product_tools = (
        {
            "type": "function",
            "name": "list_tasks",
            "description": "List tasks",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
            },
        },
    )
    with pytest.raises(MasterModelRequestError, match="changed"):
        reconstruct_model_tools(
            {
                "tools": [
                    {
                        "type": "function",
                        "name": "list_tasks",
                        "description": "Changed",
                        "strict": False,
                        "parameters": product_tools[0]["inputSchema"],
                    }
                ]
            },
            product_tools,
        )


def test_master_model_firewall_reconstructs_path_free_developer_context():
    import json

    product_tools = [
        {
            "type": "function",
            "name": "list_tasks",
            "description": "List tasks",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
            },
        }
    ]
    proxy = CodexMasterModelProxy(protected_values=("/protected/home",))
    proxy.set_product_tools(product_tools, required_names={"list_tasks"})

    for leaked in ("/protected/home/config.toml", "secret-bearer-value"):
        payload = {
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": leaked}],
                },
                    {
                        "type": "additional_tools",
                        "role": "developer",
                        "tools": [
                            {
                                "type": "function",
                                "name": "list_tasks",
                                "description": "List tasks",
                                "strict": False,
                                "parameters": product_tools[0]["inputSchema"],
                            },
                            {"type": "function", "name": "update_plan"},
                        ],
                    },
            ]
        }
        encoded, _headers = proxy._restrict_request(
            json.dumps(payload).encode(),
            {"authorization": "Bearer secret-bearer-value"},
        )
        assert leaked.encode() not in encoded
    payload = {
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {"type": "input_text", "text": "/another/host/root"}
                ],
            },
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "function",
                        "name": "list_tasks",
                        "description": "List tasks",
                        "strict": False,
                        "parameters": product_tools[0]["inputSchema"],
                    },
                    {"type": "function", "name": "update_plan"},
                ],
            },
        ]
    }
    encoded, _headers = proxy._restrict_request(
        json.dumps(payload).encode(),
        {"authorization": "Bearer unrelated-token"},
    )
    assert b"/another/host/root" not in encoded
    restricted = json.loads(encoded)
    developer = [
        item
        for item in restricted["input"]
        if item.get("role") == "developer"
        and item.get("type") != "additional_tools"
    ]
    assert len(developer) == 1
    assert reconstruct_developer_context(restricted) == restricted


def test_master_model_firewall_binds_loopback_only():
    async def go():
        proxy = CodexMasterModelProxy()
        url = await proxy.start()
        try:
            hosts = {
                socket.getsockname()[0]
                for socket in proxy._server.sockets
            }
            return url, hosts
        finally:
            await proxy.stop()

    url, hosts = asyncio.run(go())
    assert url.startswith("http://127.0.0.1:")
    assert hosts == {"127.0.0.1"}


def test_master_proxy_rejects_compression_before_decompression():
    import gzip
    import json

    tools = master_dynamic_tools()
    provider_tools = [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "strict": False,
            "parameters": tool["inputSchema"],
        }
        for tool in tools
    ]
    payload = {
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    *provider_tools,
                    {"type": "function", "name": "update_plan"},
                ],
            },
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "x" * (12 * 1024 * 1024)}
                ],
            },
        ]
    }
    proxy = CodexMasterModelProxy()
    proxy.set_product_tools(
        tools,
        required_names={tool["name"] for tool in tools},
    )

    with pytest.raises(MasterModelRequestError, match="encoding"):
        proxy._restrict_request(
            gzip.compress(json.dumps(payload).encode()),
            {"content-encoding": "gzip"},
        )


@pytest.mark.parametrize(
    "headers,error",
    [
        (
            b"Host: local\r\nContent-Length: 0\r\nContent-Length: 1\r\n",
            "duplicate headers",
        ),
        (
            b"Host: local\r\nTransfer-Encoding: chunked\r\n",
            "transfer encoding",
        ),
        (
            b"Host: local\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n",
            "transfer encoding",
        ),
    ],
)
def test_master_proxy_rejects_ambiguous_http_framing(headers, error):
    async def go():
        proxy = CodexMasterModelProxy()
        reader = asyncio.StreamReader()
        reader.feed_data(b"POST /private/v1/responses HTTP/1.1\r\n")
        reader.feed_data(headers)
        reader.feed_data(b"\r\nX")
        reader.feed_eof()
        with pytest.raises(MasterModelRequestError, match=error):
            await proxy._read_request(reader)

    asyncio.run(go())


def test_master_proxy_rejects_non_responses_routes_without_forwarding():
    async def request(path: str) -> bytes:
        proxy = CodexMasterModelProxy()
        base_url = await proxy.start()
        try:
            from urllib.parse import urlsplit

            split = urlsplit(base_url)
            reader, writer = await asyncio.open_connection(
                split.hostname,
                split.port,
            )
            writer.write(
                (
                    f"POST {path} HTTP/1.1\r\n"
                    "Host: localhost\r\n"
                    "Content-Length: 0\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            return response
        finally:
            await proxy.stop()

    response = asyncio.run(request("/not-the-private-responses-route"))

    assert response.startswith(b"HTTP/1.1 400")


def test_master_proxy_rejects_websocket_probe_with_http_fallback_signal():
    async def go():
        from urllib.parse import urlsplit

        proxy = CodexMasterModelProxy()
        base_url = await proxy.start()
        split = urlsplit(base_url)
        try:
            reader, writer = await asyncio.open_connection(
                split.hostname,
                split.port,
            )
            writer.write(
                (
                    f"GET {split.path}/responses HTTP/1.1\r\n"
                    "Host: localhost\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            return response
        finally:
            await proxy.stop()

    response = asyncio.run(go())

    assert response.startswith(b"HTTP/1.1 426")


def test_master_proxy_keeps_concurrent_connections_session_independent():
    async def go():
        from urllib.parse import urlsplit

        proxy = CodexMasterModelProxy()
        base_url = await proxy.start()
        split = urlsplit(base_url)

        async def probe():
            reader, writer = await asyncio.open_connection(
                split.hostname,
                split.port,
            )
            writer.write(
                (
                    f"GET {split.path}/responses HTTP/1.1\r\n"
                    "Host: localhost\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            return response

        try:
            return await asyncio.gather(*(probe() for _ in range(12)))
        finally:
            await proxy.stop()

    responses = asyncio.run(go())

    assert len(responses) == 12
    assert all(response.startswith(b"HTTP/1.1 426") for response in responses)


def test_master_proxy_rejects_provider_redirects():
    import httpx

    class Writer:
        def __init__(self):
            self.data = bytearray()

        def write(self, data):
            self.data.extend(data)

        async def drain(self):
            return None

    async def go():
        proxy = CodexMasterModelProxy()
        proxy._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    307,
                    headers={"location": "https://example.invalid/relay"},
                )
            )
        )
        writer = Writer()
        try:
            await proxy._forward(
                "POST",
                f"/{proxy._secret}/v1/responses",
                {"authorization": "Bearer protected"},
                b"{}",
                writer,
            )
            return bytes(writer.data)
        finally:
            await proxy._client.aclose()
            proxy._client = None

    response = asyncio.run(go())

    assert response.startswith(b"HTTP/1.1 502")
    assert b"location:" not in response.lower()
    assert b"example.invalid" not in response


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"content-encoding": "gzip"}, b"compressed"),
        (
            {"content-length": str(16 * 1024 * 1024 + 1)},
            b"not-forwarded",
        ),
        (
            [
                ("content-length", "4"),
                ("content-length", "5"),
            ],
            b"not-forwarded",
        ),
    ],
)
def test_master_proxy_buffers_and_rejects_unsafe_provider_responses(
    headers, body
):
    import gzip
    import httpx

    class Writer:
        def __init__(self):
            self.data = bytearray()

        def write(self, data):
            self.data.extend(data)

        async def drain(self):
            return None

    async def go():
        proxy = CodexMasterModelProxy()
        header_map = dict(headers)
        proxy._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        headers=headers,
                        content=(
                            gzip.compress(body)
                            if header_map.get("content-encoding") == "gzip"
                            else body
                        ),
                    )
            )
        )
        writer = Writer()
        try:
            await proxy._forward(
                "POST",
                f"/{proxy._secret}/v1/responses",
                {"authorization": "Bearer protected"},
                b"{}",
                writer,
            )
            return bytes(writer.data)
        finally:
            await proxy._client.aclose()
            proxy._client = None

    response = asyncio.run(go())

    assert response.startswith(b"HTTP/1.1 502")
    assert body not in response


def test_master_prompt_requires_pre_turn_runtime_attestation():
    async def go():
        proc = _proc(master_chat_only=True)
        with pytest.raises(
            Exception, match="contract was not attested before the turn"
        ):
            await proc.prompt(
                "unattested",
                "hello",
                lambda _update: None,
                on_dynamic_tool=lambda _name, _args: {},
            )

    asyncio.run(go())


def test_master_proxy_stop_cancels_partial_connections():
    async def go():
        proxy = CodexMasterModelProxy()
        base_url = await proxy.start()
        from urllib.parse import urlsplit

        split = urlsplit(base_url)
        reader, writer = await asyncio.open_connection(
            split.hostname,
            split.port,
        )
        writer.write(b"POST /partial HTTP/1.1\r\nHost: localhost\r\n")
        await writer.drain()
        for _ in range(20):
            if proxy._connection_tasks:
                break
            await asyncio.sleep(0)
        await proxy.stop()
        closed = await reader.read()
        writer.close()
        await writer.wait_closed()
        return closed, proxy._connection_tasks, proxy._client

    closed, tasks, client = asyncio.run(go())

    assert closed == b""
    assert tasks == set()
    assert client is None


def test_master_permission_without_active_handler_is_denied():
    process = _proc(master_chat_only=True)
    replies = []
    process._reply = lambda mid, result, error=None: replies.append(
        (mid, result, error)
    )

    process._handle_server_request(
        {
            "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "not-active"},
        }
    )

    assert replies == [(7, {"decision": "decline"}, None)]


def test_dynamic_product_tool_call_is_answered_by_registered_broker():
    async def go():
        p = _proc()
        replies = []
        p._reply = lambda mid, result, error=None: replies.append(
            (mid, result, error)
        )
        p._dynamic_tool_handlers["t1"] = (
            lambda name, arguments: {
                "ok": True,
                "tool": name,
                "result": arguments,
            }
        )
        p._handle_server_request(
            {
                "id": 9,
                "method": "item/tool/call",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn-1",
                    "callId": "call-1",
                    "tool": "list_tasks",
                    "arguments": {"limit": 3},
                },
            }
        )
        await asyncio.sleep(0)
        return replies

    replies = asyncio.run(go())
    assert replies == [
        (
            9,
            {
                "success": True,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": (
                            '{"ok":true,"tool":"list_tasks",'
                            '"result":{"limit":3}}'
                        ),
                    }
                ],
            },
            None,
        )
    ]
