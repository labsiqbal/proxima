from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


MAX_DURABLE_EVENT_PAYLOAD_BYTES = 16 * 1024


def encode_bounded_event_payload(
    payload: Mapping[str, Any],
    *,
    max_bytes: int = MAX_DURABLE_EVENT_PAYLOAD_BYTES,
) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("durable event payload is too large")
    return encoded
