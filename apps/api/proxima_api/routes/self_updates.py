"""Authenticated, feature-gated projections of the external updater."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from .. import features
from ..maintenance_status import read_external_fence
from ..safe_updates import SafeUpdateError, SafeUpdateInProgress, SafeUpdateUnmanaged

COMMIT = re.compile(r"^[a-f0-9]{40}$")


class SelfUpdateRequest(BaseModel):
    origin_job_id: str | None = Field(default=None, max_length=128)
    base_commit: str
    candidate_commit: str

    def validated(self) -> dict[str, str]:
        if not COMMIT.fullmatch(self.base_commit) or not COMMIT.fullmatch(self.candidate_commit):
            raise HTTPException(status_code=422, detail={"code": "safe_update_invalid_commit"})
        return {key: value for key, value in self.model_dump().items() if value is not None}


def register(app, deps):
    admin_user = deps["admin_user"]

    def _enabled() -> None:
        features.require(app.state.config, features.SAFE_SELF_UPDATE)

    @app.get("/api/maintenance")
    def maintenance_status(user: dict[str, Any] = Depends(admin_user)):
        """Read the updater-owned fence without treating app state as truth."""
        raw_path = app.state.config.get("safe_update_fence_path")
        if not raw_path:
            return {"active": False, "phase": None}
        value = read_external_fence(Path(str(raw_path)))
        return {"active": value is not None, **(value or {"phase": None})}

    @app.get("/api/self-updates/capability")
    def capability(user: dict[str, Any] = Depends(admin_user)):
        _enabled()
        return app.state.safe_updates.capability()

    @app.post("/api/self-updates", status_code=202)
    def submit(payload: SelfUpdateRequest, user: dict[str, Any] = Depends(admin_user)):
        _enabled()
        try:
            return app.state.safe_updates.submit(payload.validated())
        except SafeUpdateInProgress as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "run_id": exc.run_id})
        except SafeUpdateUnmanaged as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code})
        except SafeUpdateError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code})

    @app.get("/api/self-updates/{run_id}")
    def get(run_id: str, user: dict[str, Any] = Depends(admin_user)):
        _enabled()
        value = app.state.safe_updates.get(run_id)
        if value is None:
            raise HTTPException(status_code=404, detail={"code": "safe_update_not_found"})
        return value

    @app.post("/api/self-updates/{run_id}/recovery-status")
    def recovery_status(run_id: str, user: dict[str, Any] = Depends(admin_user)):
        _enabled()
        try:
            return app.state.safe_updates.recovery_status(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail={"code": "safe_update_not_found"})
