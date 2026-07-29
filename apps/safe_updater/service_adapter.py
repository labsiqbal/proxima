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


@dataclass
class DisposableServiceAdapter:
    """Test-service adapter. It is deliberately the only adapter this group can run.

    This records lifecycle calls for fault fixtures and has no manager command,
    unit name, subprocess, or production-service capability.
    """
    running_release: str
    stopped: bool = False
    autonomous_writers_paused: bool = False
    fail_at: str | None = None
    calls: list[str] | None = None
    disposable_fixture: bool = True
    previous_release: str | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []
        if self.previous_release is None:
            self.previous_release = self.running_release

    def capability(self) -> ManagerCapability:
        return ManagerCapability(False, "fixture", "disposable_test_service")

    def _call(self, value: str) -> None:
        assert self.calls is not None
        self.calls.append(value)
        if self.fail_at == value:
            raise RuntimeError(f"injected service failure: {value}")

    def pause_autonomous_writers(self) -> None:
        self._call("pause_autonomous_writers")
        self.autonomous_writers_paused = True

    def drain(self) -> None:
        self._call("drain")

    def stop_and_verify(self) -> None:
        self._call("stop_and_verify")
        self.stopped = True

    def start_readonly_candidate(self, release_id: str) -> None:
        self._call("start_readonly_candidate")
        self.running_release, self.stopped = release_id, False

    def start_writable_candidate(self, release_id: str) -> None:
        self._call("start_writable_candidate")
        self.running_release, self.stopped = release_id, False

    def stop_candidate(self) -> None:
        self._call("stop_candidate")
        self.stopped = True

    def start_previous_release(self) -> None:
        self._call("start_previous_release")
        assert self.previous_release is not None
        self.running_release = self.previous_release
        self.stopped = False

    def resume_autonomous_writers(self) -> None:
        self._call("resume_autonomous_writers")
        self.autonomous_writers_paused = False
