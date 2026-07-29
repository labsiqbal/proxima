"""Service-manager boundary.  No adapter may be treated as managed by default."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ManagerCapability:
    managed: bool
    kind: str
    reason: str | None = None


class ServiceAdapter(Protocol):
    def capability(self) -> ManagerCapability: ...
    def stop_and_verify(self) -> None: ...
    def start_readonly_candidate(self, release_id: str) -> None: ...
    def start_previous_release(self) -> None: ...
