"""Knowledge graph lifecycle: Ops allowlist freshness, debounce, and rebuild queue.

Group 11 owns at most one Knowledge graph per Container Ops area. Builds only
read the resolved Ops allowlist. Code graph lifecycle remains separate. Focus
epochs and history projection are later groups.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import features
from .maintenance_status import writes_fenced
from .graph_context import (
    GraphBuildError,
    GraphContextError,
    GraphContextService,
    _published_graph_tool_version,
)

log = logging.getLogger("proxima.knowledge_graph_lifecycle")

REASON_CONTAINER_REGISTERED = "container_registered"
REASON_OPS_TASK_DONE = "ops_task_done"
REASON_OPS_CONTENT_CHANGED = "ops_content_changed"
REASON_AUDIT = "scheduled_audit"
REASON_SCHEDULED_FULL = "scheduled_full_rebuild"
REASON_MANUAL = "manual"


class KnowledgeGraphLifecycle:
    """Background Knowledge graph orchestration for Container Ops areas."""

    def __init__(
        self,
        app: Any,
        db_factory: Callable[[], sqlite3.Connection],
        graphs: GraphContextService,
    ):
        self.app = app
        self._db_factory = db_factory
        self.graphs = graphs
        self._guard = threading.Lock()
        self._building: set[int] = set()
        self._source_markers: dict[int, str] = {}
        # container_id -> (first_dirty_monotonic, marker, content_signature)
        self._dirty_seen: dict[int, tuple[float, str, str]] = {}
        # container_id -> signature already enqueued while still mismatched
        self._dirty_enqueued: dict[int, str] = {}
        self._last_audit_at = 0.0
        self._last_full_rebuild_at = 0.0
        self._startup_audit_done = False

    @property
    def config(self) -> dict[str, Any]:
        return getattr(self.app.state, "config", {}) or {}

    def enabled(self) -> bool:
        return bool(
            features.enabled(self.config, features.MASTER_ORCHESTRATOR)
        )

    def on_container_registered(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
    ) -> None:
        """Ensure a Knowledge state row and enqueue the initial full build."""
        if not self.enabled():
            return
        try:
            self.graphs.enqueue_knowledge_rebuild(
                owner_user_id=owner_user_id,
                container_slug=container_slug,
                reason=REASON_CONTAINER_REGISTERED,
                mark_stale=False,
            )
        except GraphContextError:
            log.exception(
                "failed to enqueue Knowledge graph for container %s",
                container_slug,
            )
        except Exception:
            log.exception(
                "unexpected Knowledge graph enqueue failure for %s",
                container_slug,
            )

    def tick(self) -> None:
        if writes_fenced(getattr(self.app.state, "config", {})):
            return
        if not self.enabled():
            return
        try:
            self._reclaim_abandoned_builds()
        except Exception:
            log.exception("Knowledge graph abandoned-build reclaim failed")
        try:
            self._drain_rebuild_intents()
        except Exception:
            log.exception("Knowledge graph rebuild-intent drain failed")
        try:
            self._drain_queue(limit=1)
        except Exception:
            log.exception("Knowledge graph queue drain failed")
        try:
            self._debounce_ops_content()
        except Exception:
            log.exception("Knowledge graph content debounce failed")
        audit_every = max(
            30,
            int(self.config.get("knowledge_graph_audit_seconds", 300)),
        )
        full_every = max(
            audit_every,
            int(self.config.get("knowledge_graph_full_rebuild_seconds", 86_400)),
        )
        now = time.monotonic()
        if not self._startup_audit_done:
            self._startup_audit_done = True
            self._last_audit_at = now
            self._last_full_rebuild_at = now
            try:
                self._audit_registered_knowledge_graphs(reason=REASON_AUDIT)
            except Exception:
                log.exception("Knowledge graph startup audit failed")
        elif now - self._last_audit_at >= audit_every:
            self._last_audit_at = now
            try:
                self._audit_registered_knowledge_graphs(reason=REASON_AUDIT)
            except Exception:
                log.exception("Knowledge graph staleness audit failed")
        if now - self._last_full_rebuild_at >= full_every:
            self._last_full_rebuild_at = now
            try:
                self._schedule_full_rebuilds()
            except Exception:
                log.exception("Knowledge graph scheduled full rebuild failed")

    def _reclaim_abandoned_builds(self) -> None:
        timeout = max(
            1,
            int(self.config.get("graph_build_timeout_seconds", 120)),
        )
        grace = max(
            0,
            int(self.config.get("knowledge_graph_build_reclaim_grace_seconds", 30)),
        )
        max_age = float(timeout + grace)
        now = datetime.now(timezone.utc)
        live_ids = self.graphs.active_rebuild_ids()
        rows = self._db_factory().execute(
            "SELECT gs.id, gs.last_attempt_at "
            "FROM graph_states gs "
            "JOIN projects p ON p.id = gs.container_id "
            "WHERE gs.kind = 'knowledge' AND gs.state = 'building' "
            "AND p.archived_at IS NULL"
        ).fetchall()
        for row in rows:
            state_id = int(row["id"])
            with self._guard:
                if state_id in self._building or state_id in live_ids:
                    continue
            raw_attempt = row["last_attempt_at"]
            age_seconds: float | None
            if not raw_attempt:
                age_seconds = None
            else:
                try:
                    attempted = datetime.fromisoformat(str(raw_attempt))
                    if attempted.tzinfo is None:
                        attempted = attempted.replace(tzinfo=timezone.utc)
                    age_seconds = (now - attempted).total_seconds()
                except (TypeError, ValueError):
                    age_seconds = None
            if age_seconds is not None and age_seconds < max_age:
                continue
            live_ids = self.graphs.active_rebuild_ids()
            if state_id in live_ids:
                continue
            lock = getattr(self.app.state, "db_lock", None)
            if lock is None:
                self._mark_building_queued(state_id)
            else:
                with lock:
                    if state_id in self.graphs.active_rebuild_ids():
                        continue
                    self._mark_building_queued(state_id)

    def _mark_building_queued(self, state_id: int) -> None:
        self._db_factory().execute(
            "UPDATE graph_states SET state = 'queued', "
            "rebuild_reason = COALESCE(rebuild_reason, ?), "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND state = 'building'",
            (REASON_MANUAL, state_id),
        )
        log.info("reclaimed abandoned Knowledge graph build state_id=%s", state_id)

    def _drain_rebuild_intents(self, *, limit: int = 100) -> None:
        rows = self._db_factory().execute(
            "SELECT intent.container_id, intent.reason, intent.intent_version, "
            "p.slug, p.owner_user_id "
            "FROM knowledge_rebuild_intents intent "
            "JOIN projects p ON p.id = intent.container_id "
            "WHERE p.archived_at IS NULL "
            "ORDER BY intent.updated_at ASC, intent.container_id ASC "
            "LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        for row in rows:
            try:
                self.graphs.enqueue_knowledge_rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    reason=str(row["reason"] or REASON_OPS_TASK_DONE),
                    mark_stale=True,
                )
            except GraphContextError:
                log.exception(
                    "failed to drain Knowledge rebuild intent for container %s",
                    row["container_id"],
                )
                continue
            self._db_factory().execute(
                "DELETE FROM knowledge_rebuild_intents "
                "WHERE container_id = ? AND intent_version = ?",
                (int(row["container_id"]), int(row["intent_version"])),
            )

    def _drain_queue(self, *, limit: int = 1) -> None:
        rows = self._db_factory().execute(
            "SELECT gs.id, gs.container_id, p.slug, p.owner_user_id, "
            "gs.rebuild_reason, gs.generation, gs.state "
            "FROM graph_states gs "
            "JOIN projects p ON p.id = gs.container_id "
            "WHERE gs.kind = 'knowledge' AND gs.state = 'queued' "
            "AND p.archived_at IS NULL "
            "ORDER BY gs.updated_at ASC, gs.id ASC "
            "LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        for row in rows:
            state_id = int(row["id"])
            with self._guard:
                if state_id in self._building:
                    continue
                self._building.add(state_id)
            try:
                reason = str(row["rebuild_reason"] or REASON_MANUAL)
                self.graphs.rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    kind="knowledge",
                    area_id=None,
                    mode="full",
                    rebuild_reason=reason,
                )
            except GraphBuildError as exc:
                log.info(
                    "Knowledge graph rebuild for container %s ended: %s",
                    row["slug"],
                    exc,
                )
            except Exception:
                log.exception(
                    "Knowledge graph rebuild crashed for container %s",
                    row["slug"],
                )
            finally:
                with self._guard:
                    self._building.discard(state_id)

    def _registered_knowledge_rows(self) -> list[sqlite3.Row]:
        return self._db_factory().execute(
            "SELECT gs.id AS state_id, gs.container_id, gs.source_fingerprint, "
            "gs.tool_version, gs.generation, gs.state, gs.root_path, gs.graph_path, "
            "gs.semantic_backend, p.slug, p.owner_user_id "
            "FROM graph_states gs "
            "JOIN projects p ON p.id = gs.container_id "
            "WHERE gs.kind = 'knowledge' "
            "AND p.archived_at IS NULL "
            "ORDER BY gs.container_id"
        ).fetchall()

    def _ensure_registered_rows(self) -> None:
        """Materialize Knowledge state for Containers that never rebuilt yet."""
        missing = self._db_factory().execute(
            "SELECT p.id, p.slug, p.owner_user_id FROM projects p "
            "WHERE p.archived_at IS NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM graph_states gs "
            "  WHERE gs.container_id = p.id AND gs.kind = 'knowledge' "
            "  AND gs.area_id IS NULL"
            ") "
            "ORDER BY p.id"
        ).fetchall()
        for row in missing:
            try:
                self.graphs.enqueue_knowledge_rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    reason=REASON_CONTAINER_REGISTERED,
                    mark_stale=False,
                )
            except GraphContextError:
                log.exception(
                    "failed to register Knowledge graph for container %s",
                    row["slug"],
                )

    def _debounce_ops_content(self) -> None:
        debounce = max(
            1.0,
            float(self.config.get("knowledge_graph_dirty_debounce_seconds", 15)),
        )
        now = time.monotonic()
        for row in self._registered_knowledge_rows():
            container_id = int(row["container_id"])
            if row["state"] in {"queued", "building"}:
                continue
            try:
                marker = self.graphs.knowledge_source_marker(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                )
            except GraphContextError:
                continue
            if marker is None:
                continue
            previous_marker = self._source_markers.get(container_id)
            self._source_markers[container_id] = marker
            seen = self._dirty_seen.get(container_id)
            if previous_marker is None:
                continue
            if marker == previous_marker:
                if seen is None or seen[1] != marker:
                    continue
                if now - seen[0] < debounce:
                    continue
                signature = seen[2]
            else:
                try:
                    signature = self.graphs.knowledge_source_signature(
                        owner_user_id=int(row["owner_user_id"]),
                        container_slug=str(row["slug"]),
                    )
                except GraphContextError:
                    continue
                if signature is None:
                    continue
                published = row["source_fingerprint"] or None
                if published and signature == published:
                    self._dirty_seen.pop(container_id, None)
                    self._dirty_enqueued.pop(container_id, None)
                    continue
                if self._dirty_enqueued.get(container_id) == signature:
                    self._dirty_seen.pop(container_id, None)
                    continue
                self._dirty_seen[container_id] = (now, marker, signature)
                continue
            published = row["source_fingerprint"] or None
            if published and signature == published:
                self._dirty_seen.pop(container_id, None)
                self._dirty_enqueued.pop(container_id, None)
                continue
            if self._dirty_enqueued.get(container_id) == signature:
                self._dirty_seen.pop(container_id, None)
                continue
            try:
                self.graphs.enqueue_knowledge_rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    reason=REASON_OPS_CONTENT_CHANGED,
                    mark_stale=True,
                )
            except GraphContextError:
                log.exception(
                    "failed to enqueue dirty Knowledge rebuild for container %s",
                    row["slug"],
                )
            else:
                self._dirty_enqueued[container_id] = signature
            self._dirty_seen.pop(container_id, None)

    def _audit_registered_knowledge_graphs(self, *, reason: str) -> None:
        """Compare allowlist fingerprints for known Knowledge graphs only."""
        self._ensure_registered_rows()
        for row in self._registered_knowledge_rows():
            if row["state"] in {"queued", "building"}:
                continue
            try:
                fingerprint = self.graphs.knowledge_source_signature(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                )
            except GraphContextError:
                continue
            published = row["source_fingerprint"] or None
            # Empty allowlist (distinct fingerprint) vs prior non-empty content
            # must mark stale. Incomplete scans (None) are ignored.
            fingerprint_changed = (
                fingerprint is not None and fingerprint != published
            )
            generation = int(row["generation"] or 0)
            graph_path_raw = None
            try:
                graph_path_raw = row["graph_path"]
            except (IndexError, KeyError, TypeError):
                graph_path_raw = None
            graph_path = Path(str(graph_path_raw)) if graph_path_raw else None
            published_tool = None
            if graph_path is not None:
                published_tool = _published_graph_tool_version(
                    graph_path,
                    max_bytes=max(
                        1024,
                        int(self.config.get("graph_max_bytes", 0)),
                    ),
                )
            graph_unusable = generation > 0 and (
                graph_path is None
                or not graph_path.is_file()
                or published_tool is None
            )
            tool_mismatch = (
                generation > 0
                and published_tool is not None
                and published_tool != self.graphs.expected_tool_version()
            )
            if not (fingerprint_changed or tool_mismatch or graph_unusable):
                continue
            try:
                self.graphs.enqueue_knowledge_rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    reason=reason,
                    mark_stale=True,
                )
            except GraphContextError:
                log.exception(
                    "failed to enqueue audited Knowledge rebuild for container %s",
                    row["slug"],
                )

    def _schedule_full_rebuilds(self) -> None:
        """Periodic full rebuild of every registered Knowledge graph."""
        for row in self._registered_knowledge_rows():
            if row["state"] in {"queued", "building"}:
                continue
            try:
                self.graphs.enqueue_knowledge_rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    reason=REASON_SCHEDULED_FULL,
                    mark_stale=True,
                )
            except GraphContextError:
                log.exception(
                    "failed to schedule full Knowledge rebuild for container %s",
                    row["slug"],
                )
