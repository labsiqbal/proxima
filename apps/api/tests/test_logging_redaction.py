from __future__ import annotations

import logging

from proxima_api.logging_config import QueryTokenRedactionFilter, uvicorn_log_config


def _record(name: str, message: str, args: tuple[object, ...]) -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 1, message, args, None)


def test_query_token_filter_redacts_http_access_log() -> None:
    record = _record(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", "/events?after_id=4&token=secret-value&tail=1", "1.1", 200),
    )

    assert QueryTokenRedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "secret-value" not in rendered
    assert "/events?after_id=4&token=[REDACTED]&tail=1" in rendered


def test_query_token_filter_redacts_websocket_error_log() -> None:
    record = _record(
        "uvicorn.error",
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:1234", "/api/ws/terminal?token=secret-value&project=iqbal"),
    )

    assert QueryTokenRedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "secret-value" not in rendered
    assert "/api/ws/terminal?token=[REDACTED]&project=iqbal" in rendered


def test_uvicorn_config_filters_access_and_error_handlers() -> None:
    config = uvicorn_log_config()

    assert config["handlers"]["access"]["filters"] == ["query_token_redaction"]
    assert config["handlers"]["default"]["filters"] == ["query_token_redaction"]
