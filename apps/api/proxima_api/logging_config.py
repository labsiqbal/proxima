from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

from uvicorn.config import LOGGING_CONFIG

_QUERY_CREDENTIAL = re.compile(
    r"(?i)([?&](?:token|__proxima_cap|preview_capability)=)[^&\s\"']*"
)
_PATH_CAPABILITY = re.compile(
    r"(?i)(/_proxima/file-preview/)[^/?#\s\"']+"
)
_COOKIE_CAPABILITY = re.compile(
    r"(?i)((?:^|[;\s])proxima_file_preview(?:_[^=;\s]+)?=)[^;\s\"']+"
)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        redacted = _QUERY_CREDENTIAL.sub(r"\1[REDACTED]", value)
        redacted = _PATH_CAPABILITY.sub(r"\1[REDACTED]", redacted)
        return _COOKIE_CAPABILITY.sub(r"\1[REDACTED]", redacted)
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value


class QueryTokenRedactionFilter(logging.Filter):
    """Remove query-string auth tokens before Uvicorn formats a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        record.args = _redact(record.args)
        return True


def uvicorn_log_config() -> dict[str, Any]:
    config = deepcopy(LOGGING_CONFIG)
    config["filters"] = {
        **config.get("filters", {}),
        "query_token_redaction": {"()": "proxima_api.logging_config.QueryTokenRedactionFilter"},
    }
    for handler_name in ("default", "access"):
        handler = config["handlers"][handler_name]
        handler["filters"] = [*handler.get("filters", []), "query_token_redaction"]
    return config
