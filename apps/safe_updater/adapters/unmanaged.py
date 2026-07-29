from __future__ import annotations

from ..service_adapter import ManagerCapability


class UnmanagedAdapter:
    def capability(self) -> ManagerCapability:
        return ManagerCapability(False, "unmanaged", "safe_update_unmanaged")

    def _fail(self, *_args: str) -> None:
        raise RuntimeError("safe_update_unmanaged")

    stop_and_verify = _fail
    start_readonly_candidate = _fail
    start_previous_release = _fail
