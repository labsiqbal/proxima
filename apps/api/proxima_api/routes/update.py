"""Release update routes: status, manual check, one-click self-update."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from ..updates import NoUpdateAvailable, UpdateInProgress, UpdateUnsupported


def register(app, deps):
    admin_user = deps["admin_user"]

    @app.get("/api/update/status")
    def update_status(user: dict[str, Any] = Depends(admin_user)):
        return app.state.updates.status()

    @app.post("/api/update/check")
    async def update_check(user: dict[str, Any] = Depends(admin_user)):
        await app.state.updates.check_now()
        return app.state.updates.status()

    @app.post("/api/update/apply")
    def update_apply(user: dict[str, Any] = Depends(admin_user)):
        try:
            return app.state.updates.apply()
        except UpdateInProgress as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (NoUpdateAvailable, UpdateUnsupported) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
