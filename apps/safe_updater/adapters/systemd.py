from __future__ import annotations

import subprocess

from ..service_adapter import ManagerCapability


class SystemdAdapter:
    """Narrow contract fixture adapter. Activation remains disabled in group 14."""
    def __init__(self, unit: str, runner=subprocess.run) -> None:
        if not unit.endswith(".service") or "/" in unit:
            raise ValueError("invalid systemd unit")
        self.unit, self.runner = unit, runner

    def capability(self) -> ManagerCapability:
        return ManagerCapability(False, "systemd", "safe_update_not_enrolled")

    def _inert(self, *_args: str) -> None:
        raise RuntimeError("safe_update_activation_inert")

    stop_and_verify = _inert
    start_readonly_candidate = _inert
    start_previous_release = _inert
