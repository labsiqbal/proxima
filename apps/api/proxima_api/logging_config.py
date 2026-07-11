from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

from uvicorn.config import LOGGING_CONFIG

_QUERY_TOKEN = re.compile(r"(?i)([?&]token=)[^&\s\"']*")


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _QUERY_TOKEN.sub(r"\1[REDACTED]", value)
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
