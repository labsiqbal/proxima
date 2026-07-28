"""Code graph lifecycle: registration, merge, external drift, and rebuild queue.

Group 10 owns Code graph freshness only. Knowledge graph automation and Master
context routing remain later delivery groups. Builds always target the canonical
registered Area root - never a Task worktree.
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
from .graph_context import (
    GraphBuildError,
    GraphContextError,
    GraphContextService,
    _published_graph_tool_version,
)

log = logging.getLogger("proxima.code_graph_lifecycle")

REASON_AREA_REGISTERED = "area_registered"
REASON_TASK_MERGED = "task_merged"
REASON_EXTERNAL_HEAD = "external_head"
REASON_TRACKED_DIRTY = "tracked_dirty"
REASON_AUDIT = "scheduled_audit"
REASON_MANUAL = "manual"
REASON_INCREMENTAL_FALLBACK = "incremental_fallback"


class CodeGraphLifecycle:
    """Background Code graph orchestration for registered repo Areas."""

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
        # area_id -> (first_dirty_monotonic, last_dirty_signature)
        self._dirty_seen: dict[int, tuple[float, str]] = {}
        self._last_audit_at = 0.0

    @property
    def config(self) -> dict[str, Any]:
        return getattr(self.app.state, "config", {}) or {}

    def enabled(self) -> bool:
        return bool(
            features.enabled(self.config, features.MASTER_ORCHESTRATOR)
        )

    def on_code_areas_registered(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        area_ids: list[int],
    ) -> None:
        if not self.enabled() or not area_ids:
            return
        for area_id in area_ids:
            try:
                self.graphs.enqueue_code_rebuild(
                    owner_user_id=owner_user_id,
                    container_slug=container_slug,
                    area_id=int(area_id),
                    reason=REASON_AREA_REGISTERED,
                    mode="full",
                    mark_stale=False,
                )
            except GraphContextError:
                log.exception(
                    "failed to enqueue Code graph for area %s in %s",
                    area_id,
                    container_slug,
                )
            except Exception:
                log.exception(
                    "unexpected Code graph enqueue failure for area %s",
                    area_id,
                )

    def on_task_merged(
        self,
        *,
        owner_user_id: int,
        container_id: int,
        area_id: int,
        base_commit: str | None,
        merge_commit: str | None,
    ) -> None:
        """Mark the Task's Code graph stale immediately and enqueue rebuild."""
        if not self.enabled():
            return
        try:
            slug_row = self._db_factory().execute(
                "SELECT slug FROM projects WHERE id = ? AND owner_user_id = ?",
                (container_id, owner_user_id),
            ).fetchone()
            if slug_row is None:
                return
            self.graphs.enqueue_code_rebuild(
                owner_user_id=owner_user_id,
                container_slug=str(slug_row["slug"]),
                area_id=int(area_id),
                reason=REASON_TASK_MERGED,
                mode="incremental",
                mark_stale=True,
                pending_base_commit=base_commit,
                pending_head_commit=merge_commit,
            )
        except GraphContextError:
            log.exception(
                "failed to mark Code graph stale after merge for area %s",
                area_id,
            )
        except Exception:
            log.exception(
                "unexpected post-merge Code graph failure for area %s",
                area_id,
            )

    def tick(self) -> None:
        if not self.enabled():
            return
        try:
            self._reclaim_abandoned_builds()
        except Exception:
            log.exception("Code graph abandoned-build reclaim failed")
        try:
            self._drain_queue(limit=1)
        except Exception:
            log.exception("Code graph queue drain failed")
        try:
            self._debounce_dirty_trees()
        except Exception:
            log.exception("Code graph dirty-tree debounce failed")
        audit_every = max(
            30,
            int(self.config.get("code_graph_audit_seconds", 300)),
        )
        now = time.monotonic()
        if now - self._last_audit_at >= audit_every:
            self._last_audit_at = now
            try:
                self._audit_registered_code_graphs()
            except Exception:
                log.exception("Code graph staleness audit failed")

    def _reclaim_abandoned_builds(self) -> None:
        """Move crash-orphaned building rows back to queued so drain can resume.

        In-flight lifecycle builds are tracked in ``_building`` and left alone.
        Other ``building`` rows older than build timeout + grace (or with a
        missing/invalid attempt timestamp) are treated as abandoned after a
        process death or hung rebuild and re-queued without dropping any
        follow-up rebuild intent recorded while building.
        """
        timeout = max(
            1,
            int(self.config.get("graph_build_timeout_seconds", 120)),
        )
        grace = max(
            0,
            int(self.config.get("code_graph_build_reclaim_grace_seconds", 30)),
        )
        max_age = float(timeout + grace)
        now = datetime.now(timezone.utc)
        rows = self._db_factory().execute(
            "SELECT gs.id, gs.last_attempt_at "
            "FROM graph_states gs "
            "JOIN projects p ON p.id = gs.container_id "
            "WHERE gs.kind = 'code' AND gs.state = 'building' "
            "AND p.archived_at IS NULL"
        ).fetchall()
        for row in rows:
            state_id = int(row["id"])
            with self._guard:
                if state_id in self._building:
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
            lock = getattr(self.app.state, "db_lock", None)
            if lock is None:
                self._mark_building_queued(state_id)
            else:
                with lock:
                    self._mark_building_queued(state_id)

    def _mark_building_queued(self, state_id: int) -> None:
        self._db_factory().execute(
            "UPDATE graph_states SET state = 'queued', "
            "rebuild_reason = COALESCE(rebuild_reason, ?), "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND state = 'building'",
            (REASON_MANUAL, state_id),
        )
        log.info("reclaimed abandoned Code graph build state_id=%s", state_id)

    def _drain_queue(self, *, limit: int = 1) -> None:
        rows = self._db_factory().execute(
            "SELECT gs.id, gs.area_id, gs.container_id, p.slug, p.owner_user_id, "
            "gs.rebuild_reason, gs.pending_base_commit, gs.pending_head_commit, "
            "gs.repo_head, gs.tool_version, gs.generation, gs.state "
            "FROM graph_states gs "
            "JOIN projects p ON p.id = gs.container_id "
            "WHERE gs.kind = 'code' AND gs.state = 'queued' "
            "AND p.archived_at IS NULL "
            "ORDER BY gs.updated_at ASC, gs.id ASC "
            "LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        for row in rows:
            state_id = int(row["id"])
            area_id = int(row["area_id"])
            with self._guard:
                if state_id in self._building:
                    continue
                self._building.add(state_id)
            try:
                mode = "full"
                reason = str(row["rebuild_reason"] or REASON_MANUAL)
                if (
                    reason == REASON_TASK_MERGED
                    and row["pending_base_commit"]
                    and row["pending_head_commit"]
                    and int(row["generation"] or 0) > 0
                ):
                    mode = "incremental"
                self.graphs.rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    kind="code",
                    area_id=area_id,
                    mode=mode,
                    pending_base_commit=row["pending_base_commit"],
                    pending_head_commit=row["pending_head_commit"],
                    rebuild_reason=reason,
                )
            except GraphBuildError as exc:
                # Incremental failure already records failed/stale state; if the
                # service asked for a full fallback, re-queue once as full.
                if "fallback_full" in str(exc):
                    try:
                        self.graphs.enqueue_code_rebuild(
                            owner_user_id=int(row["owner_user_id"]),
                            container_slug=str(row["slug"]),
                            area_id=area_id,
                            reason=REASON_INCREMENTAL_FALLBACK,
                            mode="full",
                            mark_stale=True,
                        )
                    except Exception:
                        log.exception(
                            "failed to re-queue full Code graph rebuild for area %s",
                            area_id,
                        )
                else:
                    log.info(
                        "Code graph rebuild for area %s ended: %s",
                        area_id,
                        exc,
                    )
            except Exception:
                log.exception("Code graph rebuild crashed for area %s", area_id)
            finally:
                with self._guard:
                    self._building.discard(state_id)

    def _registered_code_rows(self) -> list[sqlite3.Row]:
        return self._db_factory().execute(
            "SELECT gs.id AS state_id, gs.area_id, gs.container_id, gs.repo_head, "
            "gs.source_fingerprint, gs.tool_version, gs.generation, gs.state, "
            "gs.root_path, gs.graph_path, p.slug, p.owner_user_id "
            "FROM graph_states gs "
            "JOIN projects p ON p.id = gs.container_id "
            "JOIN project_areas a ON a.id = gs.area_id "
            "WHERE gs.kind = 'code' "
            "AND a.kind = 'code' AND a.source != 'excluded' "
            "AND p.archived_at IS NULL "
            "ORDER BY gs.container_id, gs.area_id"
        ).fetchall()

    def _debounce_dirty_trees(self) -> None:
        debounce = max(
            1.0,
            float(self.config.get("code_graph_dirty_debounce_seconds", 15)),
        )
        now = time.monotonic()
        for row in self._registered_code_rows():
            area_id = int(row["area_id"])
            if row["state"] in {"queued", "building"}:
                continue
            root = Path(str(row["root_path"]))
            try:
                signature = self.graphs.tracked_dirty_signature(root)
                head = self.graphs.repo_head_sha(root)
            except GraphContextError:
                continue
            if not signature:
                self._dirty_seen.pop(area_id, None)
                continue
            seen = self._dirty_seen.get(area_id)
            if seen is None or seen[1] != signature:
                self._dirty_seen[area_id] = (now, signature)
                continue
            if now - seen[0] < debounce:
                continue
            try:
                live_fp = self.graphs.live_source_fingerprint(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    area_id=area_id,
                )
            except GraphContextError:
                live_fp = None
            published_fp = row["source_fingerprint"] or None
            if live_fp and published_fp and live_fp == published_fp:
                self._dirty_seen[area_id] = (now, signature)
                continue
            try:
                self.graphs.enqueue_code_rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    area_id=area_id,
                    reason=REASON_TRACKED_DIRTY,
                    mode="full",
                    mark_stale=True,
                    pending_head_commit=head,
                )
            except GraphContextError:
                log.exception(
                    "failed to enqueue dirty-tree Code rebuild for area %s",
                    area_id,
                )
            self._dirty_seen[area_id] = (now, signature)

    def _audit_registered_code_graphs(self) -> None:
        """Compare canonical HEAD + source fingerprint for known Code graphs only."""
        for row in self._registered_code_rows():
            if row["state"] in {"queued", "building"}:
                continue
            area_id = int(row["area_id"])
            root = Path(str(row["root_path"]))
            try:
                head = self.graphs.repo_head_sha(root)
                fingerprint = self.graphs.live_source_fingerprint(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    area_id=area_id,
                )
            except GraphContextError:
                continue
            head_changed = bool(head) and head != (row["repo_head"] or None)
            fingerprint_changed = bool(fingerprint) and fingerprint != (
                row["source_fingerprint"] or None
            )
            generation = int(row["generation"] or 0)
            # Compare against last published graph metadata only. graph_states
            # tool_version is rewritten on building/failed/queued transitions.
            published_tool = None
            graph_path_raw = None
            try:
                graph_path_raw = row["graph_path"]
            except (IndexError, KeyError, TypeError):
                graph_path_raw = None
            graph_path = Path(str(graph_path_raw)) if graph_path_raw else None
            if graph_path is not None:
                published_tool = _published_graph_tool_version(graph_path)
            graph_missing = generation > 0 and (
                graph_path is None or not graph_path.is_file()
            )
            tool_mismatch = (
                generation > 0
                and published_tool is not None
                and published_tool != self.graphs.expected_tool_version()
            )
            if not (
                head_changed
                or fingerprint_changed
                or tool_mismatch
                or graph_missing
            ):
                continue
            reason = REASON_EXTERNAL_HEAD if head_changed else REASON_AUDIT
            if tool_mismatch or graph_missing:
                reason = REASON_AUDIT
            try:
                self.graphs.enqueue_code_rebuild(
                    owner_user_id=int(row["owner_user_id"]),
                    container_slug=str(row["slug"]),
                    area_id=area_id,
                    reason=reason,
                    mode="full",
                    mark_stale=True,
                    pending_base_commit=row["repo_head"],
                    pending_head_commit=head,
                )
            except GraphContextError:
                log.exception(
                    "failed to enqueue audited Code rebuild for area %s",
                    area_id,
                )


def notify_task_merged(app: Any, wt: sqlite3.Row | dict[str, Any]) -> None:
    """Best-effort post-merge hook used by approve paths."""
    lifecycle = getattr(app.state, "code_graph_lifecycle", None)
    if lifecycle is None:
        return
    try:
        area_id = int(wt["area_id"])
        job_id = int(wt["job_id"])
        conn = lifecycle._db_factory()
        row = conn.execute(
            "SELECT j.project_id, p.owner_user_id "
            "FROM jobs j JOIN projects p ON p.id = j.project_id "
            "WHERE j.id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return
        lifecycle.on_task_merged(
            owner_user_id=int(row["owner_user_id"]),
            container_id=int(row["project_id"]),
            area_id=area_id,
            base_commit=str(wt["base_commit"] or "") or None,
            merge_commit=str(wt["merge_commit"] or "") or None,
        )
    except Exception:
        log.exception("post-merge Code graph notification failed")
