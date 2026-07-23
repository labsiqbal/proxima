"""format_rpc_error turns JSON-RPC / ACP error payloads into owner-facing text."""
from proxima_api.acp import format_rpc_error


def test_prefers_data_details_over_generic_internal_error():
    err = {
        "code": -32603,
        "message": "Internal error",
        "data": {
            "details": (
                "No LLM provider configured. Run `hermes model` to select a provider, "
                "or run `hermes setup` for first-time configuration."
            )
        },
    }
    out = format_rpc_error(err)
    assert out.startswith("No LLM provider configured")
    assert "hermes model" in out
    assert "Agents menu" in out
    assert "Internal error" not in out
    assert "-32603" not in out


def test_parses_python_repr_string_dump():
    raw = (
        "{'code': -32603, 'message': 'Internal error', "
        "'data': {'details': 'No LLM provider configured. Run `hermes model`.'}}"
    )
    out = format_rpc_error(raw)
    assert out.startswith("No LLM provider configured. Run `hermes model`")
    assert "Agents menu" in out


def test_enrichment_is_idempotent():
    once = format_rpc_error(
        "No LLM provider configured. Run `hermes setup` for first-time configuration."
    )
    assert once.count("Agents menu") == 1
    assert format_rpc_error(once) == once


def test_parses_json_string_dump():
    raw = '{"code": -32000, "message": "Internal error", "data": {"details": "boom"}}'
    assert format_rpc_error(raw) == "boom"


def test_strips_run_failed_prefix_for_clean_reprefix():
    assert format_rpc_error("Run failed: plain timeout") == "plain timeout"


def test_plain_text_passes_through():
    assert format_rpc_error("Hermes runner timed out") == "Hermes runner timed out"


def test_token_refresh_failure_gets_proxima_next_step():
    out = format_rpc_error(
        'xAI token refresh failed. Response: {"error":"invalid_grant"}'
    )
    assert "invalid_grant" in out or "token refresh failed" in out.lower()
    assert "Agents menu" in out


def test_joins_specific_message_with_details():
    err = {"message": "Provider error", "data": {"details": "quota exceeded"}}
    assert format_rpc_error(err) == "Provider error: quota exceeded"


def test_exception_instance_is_unwrapped():
    assert format_rpc_error(RuntimeError("{'message': 'Internal error', 'data': {'details': 'x'}}")) == "x"
