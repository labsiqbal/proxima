from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def with_browse_root(
    client: TestClient,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if payload.get("root_id"):
        return payload
    response = client.get("/api/fs/dirs", headers=headers)
    assert response.status_code == 200, response.text
    return {**payload, "root_id": response.json()["root_id"]}
