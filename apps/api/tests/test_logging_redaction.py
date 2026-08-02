from __future__ import annotations

import importlib
import io
import logging

from proxima_api.logging_config import (
    CredentialRedactionFilter,
    uvicorn_log_config,
)


def _record(name: str, message: str, args: tuple[object, ...]) -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 1, message, args, None)


def test_query_token_filter_redacts_http_access_log() -> None:
    record = _record(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", "/events?after_id=4&token=secret-value&tail=1", "1.1", 200),
    )

    assert CredentialRedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "secret-value" not in rendered
    assert "/events?after_id=4&token=[REDACTED]&tail=1" in rendered


def test_query_token_filter_redacts_websocket_error_log() -> None:
    record = _record(
        "uvicorn.error",
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:1234", "/api/ws/terminal?token=secret-value&project=iqbal"),
    )

    assert CredentialRedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "secret-value" not in rendered
    assert "/api/ws/terminal?token=[REDACTED]&project=iqbal" in rendered


def test_query_token_filter_redacts_app_preview_capability_forms() -> None:
    record = _record(
        "uvicorn.access",
        "%s %s",
        (
            "/site?preview_capability=alias-secret",
            "proxima_preview=cookie-secret; theme=dark",
        ),
    )

    assert CredentialRedactionFilter().filter(record)
    rendered = record.getMessage()
    for secret in ("alias-secret", "cookie-secret"):
        assert secret not in rendered
    assert "preview_capability=[REDACTED]" in rendered
    assert "proxima_preview=[REDACTED]" in rendered


def test_uvicorn_config_filters_access_and_error_handlers() -> None:
    config = uvicorn_log_config()

    assert config["handlers"]["access"]["filters"] == ["credential_redaction"]
    assert config["handlers"]["default"]["filters"] == ["credential_redaction"]


def test_plain_uvicorn_entrypoint_installs_live_capability_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        from proxima_api import main as main_module

        importlib.reload(main_module)
        logger.info(
            "GET /site?preview_capability=plain-entry-secret HTTP/1.1"
        )
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    assert "plain-entry-secret" not in output.getvalue()
    assert "preview_capability=[REDACTED]" in output.getvalue()
