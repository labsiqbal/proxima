from __future__ import annotations

from ..service_adapter import ManagerCapability


class LaunchdAdapter:
    """Contract-only until privileged helper and sandbox qualification exist."""
    def __init__(self, label: str) -> None:
        if not label.startswith("com.proxima."):
            raise ValueError("invalid launchd label")
        self.label = label

    def capability(self) -> ManagerCapability:
        return ManagerCapability(False, "launchd", "managed_but_safe_update_unavailable")

    def _inert(self, *_args: str) -> None:
        raise RuntimeError("safe_update_activation_inert")

    stop_and_verify = _inert
    start_readonly_candidate = _inert
    start_previous_release = _inert
