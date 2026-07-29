from __future__ import annotations

import json
from pathlib import Path


def read_external_fence(path: Path) -> dict[str, str] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"phase": "unknown", "reason": "maintenance_state_unreadable"}
    if not isinstance(value, dict) or not isinstance(value.get("phase"), str):
        return {"phase": "unknown", "reason": "maintenance_state_invalid"}
    return {"phase": value["phase"], "run_id": str(value.get("run_id") or "")}


def writes_fenced(config: dict[str, object]) -> bool:
    """Fail closed for unreadable controller state and never cache the answer."""
    raw_path = config.get("safe_update_fence_path")
    return bool(raw_path) and read_external_fence(Path(str(raw_path))) is not None
