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


def test_query_token_filter_redacts_every_file_preview_capability_form() -> None:
    record = _record(
        "uvicorn.access",
        "%s %s %s",
        (
            "/site?__proxima_cap=query-secret&preview_capability=alias-secret",
            "/_proxima/file-preview/path-secret/index.html",
            "proxima_file_preview_1_ops_2=cookie-secret; theme=dark",
        ),
    )

    assert QueryTokenRedactionFilter().filter(record)
    rendered = record.getMessage()
    for secret in (
        "query-secret",
        "alias-secret",
        "path-secret",
        "cookie-secret",
    ):
        assert secret not in rendered
    assert "__proxima_cap=[REDACTED]" in rendered
    assert "preview_capability=[REDACTED]" in rendered
    assert "/_proxima/file-preview/[REDACTED]/index.html" in rendered
    assert "proxima_file_preview_1_ops_2=[REDACTED]" in rendered


def test_uvicorn_config_filters_access_and_error_handlers() -> None:
    config = uvicorn_log_config()

    assert config["handlers"]["access"]["filters"] == ["query_token_redaction"]
    assert config["handlers"]["default"]["filters"] == ["query_token_redaction"]
