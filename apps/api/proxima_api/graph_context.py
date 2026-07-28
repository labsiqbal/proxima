"""Scoped Graphify adapter and safe graph-generation storage.

Public callers select a Container plus an optional Area database id. Filesystem
roots and graph paths are resolved only inside this module. Group 9 supplied the
filesystem-isolated adapter and atomic publish. Group 10 adds Code graph lifecycle helpers
(enqueue, incremental rebuild, HEAD/fingerprint, gitignore). Group 11 expands the
Ops Knowledge allowlist, Knowledge lifecycle helpers, and local-only semantic
policy labels. Master context routing lives in ``context_router``.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import multiprocessing
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping, TypeAlias

from . import container_registry
from .auth import iso_now

GRAPHIFY_DISTRIBUTION = "graphifyy"
GRAPHIFY_VERSION = "0.9.28"
GRAPH_METADATA_KEY = "proxima"
GRAPH_METADATA_SCHEMA = 1
GRAPH_STATES = frozenset(
    {"missing", "queued", "building", "fresh", "stale", "failed"}
)
GRAPH_KINDS = frozenset({"knowledge", "code"})
GRAPH_PROVENANCE = frozenset({"EXTRACTED", "INFERRED", "AMBIGUOUS"})
GRAPHIFY_GITIGNORE_LINE = "graphify-out/"
# Local structural extraction only. Cloud model egress requires an explicit
# future captain policy and a separate adapter; credentials alone never enable it.
SEMANTIC_BACKEND_LOCAL = "local-structural"
SEMANTIC_BACKEND_DISABLED = "disabled"
GraphStateRow: TypeAlias = Mapping[str, Any] | sqlite3.Row

# Knowledge graph reads ONLY these durable Ops paths (under the Ops root).
KNOWLEDGE_ROOT_FILES = frozenset({"container.md", "design.md"})
KNOWLEDGE_DIR_EXTENSIONS: dict[str, frozenset[str]] = {
    # Curated notes and decisions (living logs / generated catalogs stay out).
    "wiki": frozenset({".md"}),
    "reports": frozenset({".md", ".txt", ".html", ".json", ".rst"}),
    # Durable artifact *metadata only* - never deliverable bodies or media.
    # Explicit convention: ``*.meta.json`` or ``METADATA.md`` under artifacts/.
    "artifacts": frozenset({".json", ".md"}),
}
KNOWLEDGE_ARTIFACT_META_JSON_SUFFIX = ".meta.json"
KNOWLEDGE_ARTIFACT_META_NAMES = frozenset({"metadata.md"})
KNOWLEDGE_WIKI_EXCLUDED = frozenset({"index.md", "log.md"})
KNOWLEDGE_VCS_DIR_NAMES = frozenset({".git", ".hg", ".svn"})
KNOWLEDGE_EXCLUDED_DIR_NAMES = frozenset(
    {
        "graphify-out",
        *KNOWLEDGE_VCS_DIR_NAMES,
        "node_modules",
        "__pycache__",
        ".cache",
        "cache",
        "tmp",
        "temp",
        "tasks",
        "scripts",
        "uploads",
        "exports",
        ".proxima",
    }
)
# Distinct fingerprint for a successful allowlist walk with zero sources.
# Must not be confused with ``None`` (scan failed / incomplete).
EMPTY_KNOWLEDGE_FINGERPRINT = hashlib.sha256(
    b"proxima-knowledge-empty-allowlist-v1"
).hexdigest()
_KNOWLEDGE_SECRET_NAME_RE = re.compile(
    r"(^|[/_.-])("
    r"secret|secrets|credential|credentials|password|passwd|token|apikey|api[_-]?key|"
    r"private[_-]?key|id_rsa|id_ed25519|\.pem|\.key|\.env|dotenv"
    r")([/_.-]|$)",
    re.IGNORECASE,
)
_MAX_KNOWLEDGE_FILE_BYTES = 2 * 1024 * 1024

_MAX_ERROR_CHARS = 1000
_MAX_LABEL_CHARS = 500
_MAX_ID_CHARS = 1000
_MAX_SOURCE_FILES = 200_000
_GIT_TIMEOUT_SECONDS = 30
log = logging.getLogger("proxima.graph_context")


class GraphContextError(RuntimeError):
    code = "graph_context_error"


class GraphScopeError(GraphContextError):
    code = "graph_scope_invalid"


class GraphUnavailableError(GraphContextError):
    code = "graph_unavailable"


class GraphBuildError(GraphContextError):
    code = "graph_build_failed"


class GraphBuildTimeout(GraphBuildError):
    code = "graph_build_timeout"


class GraphValidationError(GraphBuildError):
    code = "graph_validation_failed"


class GraphQueryTimeout(GraphContextError):
    code = "graph_query_timeout"


class GraphTamperedError(GraphContextError):
    code = "graph_tampered"


@dataclass(frozen=True)
class GraphScope:
    container_id: int
    container_slug: str
    kind: str
    area_id: int | None
    area_rel_path: str | None
    root: Path
    graph_path: Path
    excluded_roots: tuple[Path, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "container_id": self.container_id,
            "container_slug": self.container_slug,
            "kind": self.kind,
            "area_id": self.area_id,
            "area_rel_path": self.area_rel_path,
        }

    def metadata(
        self,
        generation: int,
        source_fingerprint: str,
        *,
        semantic_backend: str = SEMANTIC_BACKEND_LOCAL,
    ) -> dict[str, Any]:
        return {
            "schema": GRAPH_METADATA_SCHEMA,
            "container_id": self.container_id,
            "kind": self.kind,
            "area_id": self.area_id,
            "generation": generation,
            "source_fingerprint": source_fingerprint,
            "tool_version": GRAPHIFY_VERSION,
            "semantic_backend": semantic_backend,
            "complete": True,
        }


@dataclass(frozen=True)
class GraphQueryBudgets:
    depth: int
    timeout_ms: int
    token_budget: int
    result_limit: int


def _contains(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _graph_path(root: Path, *, create: bool) -> Path:
    root = root.resolve(strict=True)
    output = root / "graphify-out"
    if output.is_symlink():
        raise GraphScopeError("graph output directory cannot be a symlink")
    if output.exists() and not output.is_dir():
        raise GraphScopeError("graph output path is not a directory")
    if create:
        output.mkdir(parents=False, exist_ok=True)
        ensure_graphify_gitignore(root)
    if output.exists():
        resolved_output = output.resolve(strict=True)
        if not _contains(root, resolved_output):
            raise GraphScopeError("graph output directory escapes its scope")
    graph = output / "graph.json"
    if graph.is_symlink():
        raise GraphScopeError("canonical graph cannot be a symlink")
    return graph


def _repo_exclude_path(root: Path) -> Path | None:
    """Return this checkout's repo-local exclude file when git metadata is present."""
    git_meta = root / ".git"
    if git_meta.is_dir():
        return git_meta / "info" / "exclude"
    if not git_meta.is_file():
        return None
    try:
        text = git_meta.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.lower().startswith("gitdir:"):
            raw = line.split(":", 1)[1].strip()
            if not raw:
                return None
            gitdir = Path(raw)
            if not gitdir.is_absolute():
                gitdir = (root / gitdir).resolve()
            return gitdir / "info" / "exclude"
    return None


def _is_graphify_ignore_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").strip().strip("/")
    if normalized in {".gitignore", "graphify-out"}:
        return True
    return normalized.startswith("graphify-out/")


def _ensure_ignore_line(path: Path) -> None:
    if path.is_symlink():
        return
    existing = ""
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        for line in existing.splitlines():
            if line.strip() in {
                "graphify-out/",
                "graphify-out",
                "/graphify-out/",
                "/graphify-out",
            }:
                return
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(
        f"{existing}{suffix}# Proxima Code graph build output\n"
        f"{GRAPHIFY_GITIGNORE_LINE}\n",
        encoding="utf-8",
    )


def ensure_graphify_gitignore(root: Path) -> None:
    """Keep generated graph artifacts as ignored build outputs inside the repo.

    Prefer the repo-local exclude file so Proxima never dirties a tracked
    ``.gitignore`` (which would loop Code graph dirty-tree rebuilds forever).
    Fall back to ``.gitignore`` only when this path is not a git checkout.
    """
    path = _repo_exclude_path(root) or (root / ".gitignore")
    try:
        _ensure_ignore_line(path)
    except OSError:
        log.warning("could not ensure graphify-out ignore under %s", root)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise GraphScopeError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GraphScopeError("git timed out while inspecting the Code Area") from exc
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()[-300:]
        raise GraphScopeError(f"git {' '.join(args[:2])} failed: {detail}")
    return res


def _is_worktree_path(path: Path, workspace_root: Path | None) -> bool:
    if workspace_root is None:
        return False
    try:
        worktrees = (workspace_root / "worktrees").resolve()
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == worktrees or worktrees in resolved.parents


def _safe_rel_source(value: Any) -> PurePosixPath:
    text = str(value or "").strip()
    windows = PureWindowsPath(text)
    path = PurePosixPath(text.replace("\\", "/"))
    if (
        not text
        or "\x00" in text
        or "://" in text
        or path.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise GraphValidationError("graph contains an unsafe source reference")
    return path


def _resolve_source(
    root: Path,
    value: Any,
    excluded_roots: tuple[Path, ...] = (),
) -> tuple[str, Path]:
    rel = _safe_rel_source(value)
    try:
        target = root.joinpath(*rel.parts).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GraphValidationError(
            "graph cites a source that is not present in its scope"
        ) from exc
    if (
        not target.is_file()
        or not _contains(root, target)
        or any(_contains(excluded, target) for excluded in excluded_roots)
    ):
        raise GraphValidationError("graph source escapes its registered scope")
    return rel.as_posix(), target


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint_files(
    root: Path,
    rel_paths: list[str],
    excluded_roots: tuple[Path, ...] = (),
) -> str:
    digest = hashlib.sha256()
    for rel_text in sorted(set(rel_paths)):
        rel, target = _resolve_source(root, rel_text, excluded_roots)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(target.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_hash_file(target).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _json_bytes(data: Any) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_bytes_fsync(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def _installed_graphify_version() -> str | None:
    try:
        return importlib.metadata.version(GRAPHIFY_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return None


def _published_graph_tool_version(graph_path: Path) -> str | None:
    """Return tool_version from the last successfully published graph.

    Reads canonical graph.json metadata only. Failed rebuilds must not rewrite
    this pin; graph_states.tool_version is not authoritative for incremental
    eligibility because transitions overwrite it with the installed pin.
    """
    if graph_path.is_symlink() or not graph_path.is_file():
        return None
    try:
        data = json.loads(graph_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    graph_meta = data.get("graph")
    if not isinstance(graph_meta, dict):
        return None
    metadata = graph_meta.get(GRAPH_METADATA_KEY)
    if not isinstance(metadata, dict):
        return None
    tool_version = metadata.get("tool_version")
    if not isinstance(tool_version, str) or not tool_version:
        return None
    return tool_version


def _coalesce_pending_range(
    existing_base: str | None,
    existing_head: str | None,
    new_base: str | None,
    new_head: str | None,
) -> tuple[str | None, str | None]:
    """Widen an unapplied pending commit window instead of replacing it.

    Keep the earliest non-null base (widest coverage), advance head when a newer
    head arrives, and never let a no-range enqueue clear an existing range.
    """
    base = existing_base if existing_base is not None else new_base
    head = new_head if new_head is not None else existing_head
    return base, head


def _coalesce_rebuild_reason(
    existing_reason: str | None,
    incoming_reason: str,
) -> str:
    """Prefer full-required rebuild reasons over later incremental intents.

    Drain only selects incremental when reason is exactly ``task_merged``.
    A sticky full reason (incremental_fallback, audit, external_head, …) must
    not be downgraded by a subsequent task_merged enqueue.
    """
    existing = str(existing_reason or "").strip()
    incoming = str(incoming_reason or "").strip()
    if not existing:
        return incoming or "manual"
    if not incoming:
        return existing
    if incoming == "task_merged" and existing != "task_merged":
        return existing
    return incoming


def _is_knowledge_secret_path(rel_path: str) -> bool:
    return bool(_KNOWLEDGE_SECRET_NAME_RE.search(rel_path.replace("\\", "/")))


def _is_vcs_tree(path: Path) -> bool:
    """True when path is a VCS metadata dir or a nested repo root."""
    if path.name in KNOWLEDGE_VCS_DIR_NAMES:
        return True
    try:
        if not path.is_dir() or path.is_symlink():
            return False
    except OSError:
        return False
    for marker in KNOWLEDGE_VCS_DIR_NAMES:
        candidate = path / marker
        try:
            if candidate.exists():
                return True
        except OSError:
            continue
    return False


def _knowledge_path_allowed(rel_path: str) -> bool:
    """True when a scope-relative path is on the durable Knowledge allowlist."""
    normalized = rel_path.replace("\\", "/").strip().strip("/")
    if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
        return False
    if any(
        part in KNOWLEDGE_EXCLUDED_DIR_NAMES
        for part in PurePosixPath(normalized).parts
    ):
        return False
    if _is_graphify_ignore_path(normalized) or _is_knowledge_secret_path(normalized):
        return False
    if normalized in KNOWLEDGE_ROOT_FILES:
        return True
    pure = PurePosixPath(normalized)
    if not pure.parts:
        return False
    top = pure.parts[0]
    if top not in KNOWLEDGE_DIR_EXTENSIONS:
        return False
    if top == "wiki":
        # Generated catalog and living session log are not durable knowledge.
        name = pure.name
        if name in KNOWLEDGE_WIKI_EXCLUDED or pure.as_posix().endswith("/log.md"):
            return False
    if top == "artifacts":
        # Explicit durable metadata convention only - never deliverable bodies.
        name = pure.name.lower()
        if name in KNOWLEDGE_ARTIFACT_META_NAMES:
            return True
        return name.endswith(KNOWLEDGE_ARTIFACT_META_JSON_SUFFIX)
    suffix = pure.suffix.lower()
    return suffix in KNOWLEDGE_DIR_EXTENSIONS[top]


def _select_knowledge_sources(
    root: Path,
    excluded_roots: tuple[Path, ...] = (),
) -> tuple[list[str], list[str]]:
    """Walk only allowlisted durable Ops knowledge; fail closed on incomplete scans."""
    errors: list[str] = []
    rel_paths: list[str] = []
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return [], [f"Knowledge Ops root is unavailable: {exc}"]

    def consider(path: Path) -> None:
        if path.is_symlink():
            return
        try:
            if not path.is_file():
                return
            resolved = path.resolve(strict=True)
            if not _contains(resolved_root, resolved):
                errors.append("Knowledge source escaped the Ops root")
                return
            if any(_contains(excluded, resolved) for excluded in excluded_roots):
                return
            rel = resolved.relative_to(resolved_root).as_posix()
        except (OSError, RuntimeError, ValueError):
            errors.append("Knowledge source could not be resolved inside Ops")
            return
        if not _knowledge_path_allowed(rel):
            return
        try:
            size = resolved.stat().st_size
        except OSError:
            errors.append("Knowledge source became unreadable during scan")
            return
        if size <= 0 or size > _MAX_KNOWLEDGE_FILE_BYTES:
            # Oversized or empty files are skipped, not fatal - still fail if
            # the walk itself is incomplete.
            return
        rel_paths.append(rel)
        if len(rel_paths) > _MAX_SOURCE_FILES:
            raise GraphBuildError("graph source count exceeds the server limit")

    for name in sorted(KNOWLEDGE_ROOT_FILES):
        candidate = resolved_root / name
        if candidate.exists() or candidate.is_symlink():
            consider(candidate)

    for dirname in sorted(KNOWLEDGE_DIR_EXTENSIONS):
        base = resolved_root / dirname
        if base.is_symlink():
            continue
        if not base.is_dir():
            continue
        # A nested repository rooted at an allowlisted top-level dir is out.
        if _is_vcs_tree(base):
            continue
        stack = [base]
        while stack:
            current = stack.pop()
            if current.is_symlink():
                continue
            try:
                entries = list(current.iterdir())
            except OSError:
                errors.append("Knowledge directory walk was incomplete")
                continue
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.name in KNOWLEDGE_EXCLUDED_DIR_NAMES:
                    continue
                if entry.name.startswith(".") and entry.name not in {".md"}:
                    # Hidden dirs/files are never durable Knowledge sources.
                    if entry.is_dir() or entry.is_file():
                        continue
                try:
                    if entry.is_dir():
                        # Nested VCS / runtime trees never enter the allowlist.
                        if _is_vcs_tree(entry):
                            continue
                        stack.append(entry)
                    elif entry.is_file():
                        consider(entry)
                except OSError:
                    errors.append("Knowledge directory walk was incomplete")

    return sorted(set(rel_paths)), errors


def _select_graphify_sources(
    root: Path,
    kind: str,
    cache_root: Path,
    excluded_roots: tuple[Path, ...] = (),
) -> tuple[list[str], list[str]]:
    """Return exact scope-relative source paths and incomplete-scan diagnostics."""
    if kind == "knowledge":
        return _select_knowledge_sources(root, excluded_roots)

    from graphify.detect import detect

    detection = detect(
        root,
        follow_symlinks=False,
        cache_root=cache_root,
    )
    errors = [str(item) for item in detection.get("walk_errors") or []]
    rel_paths: list[str] = []
    for raw in detection.get("files", {}).get("code", []):
        path = Path(raw)
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve(strict=True)
            rel = resolved.relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            errors.append("Graphify detected a code source outside the Area")
            continue
        if any(_contains(excluded, resolved) for excluded in excluded_roots):
            continue
        rel_paths.append(rel)
        if len(rel_paths) > _MAX_SOURCE_FILES:
            raise GraphBuildError("graph source count exceeds the server limit")
    return sorted(set(rel_paths)), errors


def semantic_backend_label(*, kind: str, egress_enabled: bool) -> str:
    """Record which extraction backend a generation used (or refused)."""
    del kind  # Code and Knowledge both stay local-structural in this group.
    if egress_enabled:
        # Explicit opt-in is visible, but cloud extraction is still refused.
        return SEMANTIC_BACKEND_DISABLED
    return SEMANTIC_BACKEND_LOCAL


def _build_graphify_worker(
    result_queue: Any,
    *,
    root_text: str,
    stage_text: str,
    kind: str,
    metadata: dict[str, Any],
    excluded_root_texts: list[str],
    mode: str = "full",
    base_graph_text: str | None = None,
    changed_rel_paths: list[str] | None = None,
    deleted_rel_paths: list[str] | None = None,
) -> None:
    try:
        import graphify
        from graphify.build import build_merge

        root = Path(root_text).resolve(strict=True)
        excluded_roots = tuple(
            Path(value).resolve(strict=True) for value in excluded_root_texts
        )
        stage = Path(stage_text)
        rel_paths, walk_errors = _select_graphify_sources(
            root,
            kind,
            stage.parent,
            excluded_roots,
        )
        if walk_errors:
            raise GraphBuildError("Graphify could not completely enumerate the scope")
        if not rel_paths:
            raise GraphBuildError("no local structural sources are available")
        fingerprint = _fingerprint_files(root, rel_paths, excluded_roots)
        expected = dict(metadata)
        expected["source_fingerprint"] = fingerprint

        if mode == "incremental":
            if not base_graph_text:
                raise GraphBuildError("incremental rebuild requires a base graph")
            base_graph = Path(base_graph_text)
            if base_graph.is_symlink() or not base_graph.is_file():
                raise GraphBuildError("incremental base graph is unavailable")
            changed = list(changed_rel_paths or [])
            deleted = list(deleted_rel_paths or [])
            if not changed and not deleted:
                raise GraphBuildError("incremental rebuild has no changed files")
            # Only extract paths that still exist in the current scope.
            extract_rels = [
                rel for rel in changed if rel in set(rel_paths)
            ]
            sources = [
                root.joinpath(*PurePosixPath(rel).parts) for rel in extract_rels
            ]
            if sources:
                extracted = graphify.extract(
                    sources,
                    cache_root=stage.parent,
                    root=root,
                    parallel=True,
                )
                chunks = extracted if isinstance(extracted, list) else [extracted]
            else:
                chunks = [{"nodes": [], "edges": [], "hyperedges": []}]
            merge_base = stage.parent / "base-graph.json"
            shutil.copy2(base_graph, merge_base)
            graph = build_merge(
                chunks,
                graph_path=merge_base,
                prune_sources=[
                    str(root.joinpath(*PurePosixPath(rel).parts)) for rel in deleted
                ]
                or None,
                directed=True,
                root=root,
            )
        else:
            sources = [root.joinpath(*PurePosixPath(rel).parts) for rel in rel_paths]
            extracted = graphify.extract(
                sources,
                cache_root=stage.parent,
                root=root,
                parallel=True,
            )
            graph = graphify.build_from_json(
                extracted,
                directed=True,
                root=root,
            )

        if graph.number_of_nodes() <= 0:
            raise GraphBuildError("Graphify produced an empty graph")
        if not graphify.to_json(
            graph,
            {},
            str(stage),
            force=True,
        ):
            raise GraphBuildError("Graphify refused to write the generation")
        data = json.loads(stage.read_text(encoding="utf-8"))
        graph_meta = data.setdefault("graph", {})
        if not isinstance(graph_meta, dict):
            raise GraphValidationError("Graphify graph metadata is malformed")
        graph_meta[GRAPH_METADATA_KEY] = expected
        _write_bytes_fsync(stage, _json_bytes(data))
        result_queue.put(
            {
                "ok": True,
                "source_files": rel_paths,
                "source_fingerprint": fingerprint,
                "nodes": graph.number_of_nodes(),
                "edges": graph.number_of_edges(),
                "mode": mode,
            }
        )
    except BaseException as exc:
        result_queue.put(
            {
                "ok": False,
                "code": getattr(exc, "code", "graph_build_failed"),
                "error": str(exc) or type(exc).__name__,
            }
        )


def _validate_graph_data(
    path: Path,
    *,
    root: Path,
    expected_metadata: Mapping[str, Any],
    max_bytes: int,
    excluded_roots: tuple[Path, ...] = (),
) -> tuple[dict[str, Any], str]:
    try:
        stat = path.lstat()
    except OSError as exc:
        raise GraphValidationError("generated graph is missing") from exc
    if path.is_symlink() or not path.is_file():
        raise GraphValidationError("generated graph is not a regular file")
    if stat.st_size <= 0 or stat.st_size > max_bytes:
        raise GraphValidationError("generated graph exceeds its byte budget")
    payload = path.read_bytes()
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GraphValidationError("generated graph is malformed JSON") from exc
    if not isinstance(data, dict):
        raise GraphValidationError("generated graph root must be an object")
    nodes = data.get("nodes")
    links = data.get("links", data.get("edges"))
    graph_meta = data.get("graph")
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise GraphValidationError("generated graph has no node/link contract")
    if not isinstance(graph_meta, dict):
        raise GraphValidationError("generated graph metadata is missing")
    metadata = graph_meta.get(GRAPH_METADATA_KEY)
    if not isinstance(metadata, dict) or metadata != dict(expected_metadata):
        raise GraphValidationError("generated graph belongs to the wrong scope")
    if metadata.get("complete") is not True:
        raise GraphValidationError("generated graph is incomplete")

    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise GraphValidationError("generated graph contains a malformed node")
        node_id = node.get("id")
        if (
            not isinstance(node_id, str)
            or not node_id
            or len(node_id) > _MAX_ID_CHARS
            or node_id in node_ids
        ):
            raise GraphValidationError("generated graph contains an invalid node id")
        node_ids.add(node_id)
        label = node.get("label")
        if label is not None and (
            not isinstance(label, str) or len(label) > _MAX_LABEL_CHARS
        ):
            raise GraphValidationError("generated graph contains an invalid label")
        if node.get("source_file") is not None:
            _resolve_source(root, node["source_file"], excluded_roots)

    for link in links:
        if not isinstance(link, dict):
            raise GraphValidationError("generated graph contains a malformed edge")
        if link.get("source") not in node_ids or link.get("target") not in node_ids:
            raise GraphValidationError("generated graph contains a dangling edge")
        if link.get("source_file") is not None:
            _resolve_source(root, link["source_file"], excluded_roots)
        confidence = str(link.get("confidence") or "EXTRACTED").upper()
        if confidence not in GRAPH_PROVENANCE:
            raise GraphValidationError("generated graph has unknown edge provenance")

    serialized = payload.decode("utf-8", "strict")
    root_spellings = {str(root), root.as_posix()}
    if any(spelling and spelling in serialized for spelling in root_spellings):
        raise GraphValidationError("generated graph leaks an absolute scope path")
    return data, hashlib.sha256(payload).hexdigest()


def _citation(
    node: Mapping[str, Any],
    root: Path,
    excluded_roots: tuple[Path, ...],
) -> dict[str, str] | None:
    source = node.get("source_file")
    if source is None:
        return None
    rel, _ = _resolve_source(root, source, excluded_roots)
    line = str(node.get("source_location") or "").strip()[:80]
    return {
        "path": rel,
        "path_kind": "scope_relative",
        "location": line,
    }


def _query_graph_data(
    path: Path,
    *,
    root: Path,
    expected_metadata: Mapping[str, Any],
    expected_sha256: str,
    max_bytes: int,
    question: str,
    budgets: GraphQueryBudgets,
    scope: dict[str, Any],
    freshness: dict[str, Any],
    excluded_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + budgets.timeout_ms / 1000

    def check_time() -> None:
        if time.monotonic() > deadline:
            raise GraphQueryTimeout("graph query exceeded its time budget")

    data, actual_sha256 = _validate_graph_data(
        path,
        root=root,
        expected_metadata=expected_metadata,
        max_bytes=max_bytes,
        excluded_roots=excluded_roots,
    )
    if actual_sha256 != expected_sha256:
        raise GraphTamperedError(
            "canonical graph no longer matches its published digest"
        )
    check_time()
    nodes = data["nodes"]
    links = data.get("links", data.get("edges", []))
    by_id = {str(node["id"]): node for node in nodes}
    terms = [
        token
        for token in re.findall(r"\w+", question.casefold(), flags=re.UNICODE)
        if token
    ]
    scored: list[tuple[int, str]] = []
    for index, (node_id, node) in enumerate(by_id.items()):
        if index % 256 == 0:
            check_time()
        label = str(node.get("label") or node_id).casefold()
        source = str(node.get("source_file") or "").casefold()
        score = 0
        joined = " ".join(terms)
        if joined and label == joined:
            score += 1000
        elif joined and label.startswith(joined):
            score += 250
        elif joined and joined in label:
            score += 100
        for term in terms:
            if label == term:
                score += 100
            elif label.startswith(term):
                score += 40
            elif term in label:
                score += 20
            if term in source:
                score += 5
        if score:
            scored.append((score, node_id))
    scored.sort(
        key=lambda item: (
            -item[0],
            len(str(by_id[item[1]].get("label") or item[1])),
            item[1],
        )
    )
    seeds = [node_id for _, node_id in scored[:5]]

    adjacency: dict[str, list[tuple[str, Mapping[str, Any]]]] = {
        node_id: [] for node_id in by_id
    }
    for index, link in enumerate(links):
        if index % 256 == 0:
            check_time()
        source = str(link["source"])
        target = str(link["target"])
        adjacency[source].append((target, link))
        adjacency[target].append((source, link))

    selected: list[str] = []
    distance: dict[str, int] = {}
    pending: deque[str] = deque()
    for seed in seeds:
        if seed not in distance:
            distance[seed] = 0
            pending.append(seed)
    while pending and len(selected) < budgets.result_limit:
        check_time()
        node_id = pending.popleft()
        selected.append(node_id)
        depth = distance[node_id]
        if depth >= budgets.depth:
            continue
        for neighbor, _ in adjacency[node_id]:
            if neighbor not in distance:
                distance[neighbor] = depth + 1
                pending.append(neighbor)
    selected_set = set(selected)

    selected_links = [
        link
        for link in links
        if str(link["source"]) in selected_set
        and str(link["target"]) in selected_set
    ]
    provenance_counts = {name: 0 for name in sorted(GRAPH_PROVENANCE)}
    relation_by_node: dict[str, list[dict[str, Any]]] = {
        node_id: [] for node_id in selected
    }
    for link in selected_links:
        confidence = str(link.get("confidence") or "EXTRACTED").upper()
        provenance_counts[confidence] += 1
        source = str(link["source"])
        target = str(link["target"])
        relation = str(link.get("relation") or link.get("context") or "related")
        relation_by_node[source].append(
            {
                "direction": "out",
                "relation": relation[:120],
                "node_id": target,
                "provenance": confidence,
            }
        )
        relation_by_node[target].append(
            {
                "direction": "in",
                "relation": relation[:120],
                "node_id": source,
                "provenance": confidence,
            }
        )

    citations: list[dict[str, str]] = []
    citation_keys: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for node_id in selected:
        node = by_id[node_id]
        node_citation = _citation(node, root, excluded_roots)
        node_citations = [node_citation] if node_citation else []
        if node_citation:
            key = (node_citation["path"], node_citation["location"])
            if key not in citation_keys:
                citation_keys.add(key)
                citations.append(node_citation)
        relations = relation_by_node[node_id]
        node_provenance = sorted(
            {str(item["provenance"]) for item in relations}
            or {
                "EXTRACTED"
                if str(node.get("_origin") or "").lower() == "ast"
                else "INFERRED"
            }
        )
        items.append(
            {
                "id": node_id,
                "label": str(node.get("label") or node_id),
                "type": str(node.get("file_type") or "unknown")[:80],
                "distance": distance.get(node_id, 0),
                "citations": node_citations,
                "provenance": node_provenance,
                "relations": relations[:20],
            }
        )

    result = {
        "scope": scope,
        "generation": freshness["generation"],
        "freshness": freshness,
        "limits": {
            "depth": budgets.depth,
            "timeout_ms": budgets.timeout_ms,
            "token_budget": budgets.token_budget,
            "result_limit": budgets.result_limit,
        },
        "items": items,
        "citations": citations,
        "provenance": [
            {"kind": name, "edge_count": count}
            for name, count in provenance_counts.items()
        ],
        "truncated": len(distance) > len(selected),
        "elapsed_ms": 0,
        "error": None,
    }
    max_chars = budgets.token_budget * 4
    while result["items"] and len(_json_bytes(result)) > max_chars:
        removed = result["items"].pop()
        removed_citations = {
            (item["path"], item["location"])
            for item in removed.get("citations", [])
        }
        if removed_citations:
            still_used = {
                (item["path"], item["location"])
                for item in result["items"]
                for item in item.get("citations", [])
            }
            result["citations"] = [
                item
                for item in result["citations"]
                if (item["path"], item["location"]) in still_used
            ]
        result["truncated"] = True
    result["elapsed_ms"] = max(0, int((time.monotonic() - started) * 1000))
    check_time()
    return result


def _query_worker(result_queue: Any, kwargs: dict[str, Any]) -> None:
    try:
        kwargs = dict(kwargs)
        kwargs["path"] = Path(kwargs["path"])
        kwargs["root"] = Path(kwargs["root"])
        kwargs["excluded_roots"] = tuple(
            Path(value) for value in kwargs.get("excluded_roots", [])
        )
        kwargs["budgets"] = GraphQueryBudgets(**kwargs["budgets"])
        result_queue.put({"ok": True, "result": _query_graph_data(**kwargs)})
    except BaseException as exc:
        result_queue.put(
            {
                "ok": False,
                "code": getattr(exc, "code", "graph_query_failed"),
                "error": str(exc) or type(exc).__name__,
            }
        )


class GraphContextService:
    """One filesystem-isolated boundary for Graphify state, rebuild, and query."""

    def __init__(self, app: Any, db_factory: Callable[[], sqlite3.Connection]):
        self.app = app
        self._db_factory = db_factory
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[int, str, int | None], threading.Lock] = {}
        # graph_state ids with an in-process rebuild holding the scope lock
        # (API routes and lifecycle drain). Reclaim must not flip these to queued.
        self._active_rebuild_ids: set[int] = set()

    @property
    def config(self) -> Mapping[str, Any]:
        return getattr(self.app.state, "config", {}) or {}

    def expected_tool_version(self) -> str:
        return GRAPHIFY_VERSION

    def active_rebuild_ids(self) -> frozenset[int]:
        with self._locks_guard:
            return frozenset(self._active_rebuild_ids)

    def _lock_for(self, scope: GraphScope) -> threading.Lock:
        key = (scope.container_id, scope.kind, scope.area_id)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def repo_head_sha(self, root: Path) -> str | None:
        if not (root / ".git").exists():
            return None
        res = _git(root, "rev-parse", "HEAD", check=False)
        if res.returncode != 0:
            return None
        head = res.stdout.strip()
        return head or None

    def tracked_dirty_signature(self, root: Path) -> str | None:
        """Fingerprint dirty tracked paths only (ignores untracked noise).

        ``.gitignore`` / ``graphify-out`` bootstrap noise is excluded so the
        lifecycle never rebuilds solely because Proxima ensured ignores.
        """
        if not (root / ".git").exists():
            return None
        res = _git(root, "status", "--porcelain", "--untracked-files=no", check=False)
        if res.returncode != 0:
            return None
        lines: list[str] = []
        for line in res.stdout.splitlines():
            if not line.strip():
                continue
            path_part = line[3:] if len(line) >= 4 else line
            if " -> " in path_part:
                path_part = path_part.split(" -> ", 1)[1]
            path_part = path_part.strip().strip('"')
            if _is_graphify_ignore_path(path_part):
                continue
            lines.append(line)
        if not lines:
            return None
        digest = hashlib.sha256()
        for line in sorted(lines):
            digest.update(line.encode("utf-8", "replace"))
            digest.update(b"\n")
        return digest.hexdigest()

    def incremental_sources_covered_by_commit(
        self,
        scope: GraphScope,
        commit: str,
    ) -> bool:
        """True when every live Graphify source is in the claimed commit tree.

        Incremental merge only extracts ``base..head``. Untracked or otherwise
        detect-only sources would be fingerprinted from the live tree but never
        extracted, so eligibility and pre-publish must fail closed to full
        rebuild when any live source is missing from the commit tree.
        """
        head = str(commit or "").strip()
        if not head or scope.kind != "code":
            return False
        if not (scope.root / ".git").exists():
            return False
        listed = _git(
            scope.root,
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            head,
            check=False,
        )
        if listed.returncode != 0:
            return False
        tree_paths = {
            part.replace("\\", "/")
            for part in listed.stdout.split("\0")
            if part.strip()
        }
        cache = Path(tempfile.mkdtemp(prefix=".proxima-inc-cover-"))
        try:
            try:
                rel_paths, errors = _select_graphify_sources(
                    scope.root,
                    scope.kind,
                    cache,
                    scope.excluded_roots,
                )
            except GraphBuildError:
                return False
            if errors:
                return False
            return all(rel in tree_paths for rel in rel_paths)
        finally:
            shutil.rmtree(cache, ignore_errors=True)

    def changed_files_between(
        self,
        root: Path,
        base: str,
        head: str,
    ) -> tuple[list[str], list[str], str | None]:
        """Return (changed_rel_paths, deleted_rel_paths, full_rebuild_reason)."""
        if not base or not head:
            return [], [], "missing commit range"
        merge_base = _git(root, "merge-base", base, head, check=False)
        if merge_base.returncode != 0:
            return [], [], "unknown history"
        if merge_base.stdout.strip() != base:
            return [], [], "force-push or non-fast-forward history"
        status = _git(
            root,
            "diff",
            "--name-status",
            "--find-renames",
            f"{base}..{head}",
            check=False,
        )
        if status.returncode != 0:
            return [], [], "diff failed"
        changed: list[str] = []
        removed: list[str] = []
        for line in status.stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            parts = raw.split("\t")
            code = parts[0].strip() if parts else ""
            if not code:
                continue
            kind = code[0].upper()
            if kind in {"R", "C"}:
                if len(parts) < 3:
                    return [], [], "diff failed"
                old_path = parts[1].strip().replace("\\", "/")
                new_path = parts[2].strip().replace("\\", "/")
                if kind == "R" and old_path:
                    removed.append(old_path)
                elif kind == "C" and old_path:
                    changed.append(old_path)
                if new_path:
                    changed.append(new_path)
            elif kind == "D":
                if len(parts) < 2:
                    return [], [], "diff failed"
                path = parts[1].strip().replace("\\", "/")
                if path:
                    removed.append(path)
            elif kind in {"A", "M", "T"}:
                if len(parts) < 2:
                    return [], [], "diff failed"
                path = parts[1].strip().replace("\\", "/")
                if path:
                    changed.append(path)
            else:
                return [], [], f"unsupported diff status {code}"
        return changed, removed, None

    def live_source_fingerprint(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        area_id: int | None = None,
        kind: str = "code",
    ) -> str | None:
        scope = self.resolve_scope(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind=kind,
            area_id=area_id,
            create_output=False,
        )
        cache = Path(tempfile.mkdtemp(prefix=".proxima-fp-"))
        try:
            rel_paths, errors = _select_graphify_sources(
                scope.root,
                kind,
                cache,
                scope.excluded_roots,
            )
            if errors or not rel_paths:
                return None
            return _fingerprint_files(scope.root, rel_paths, scope.excluded_roots)
        finally:
            shutil.rmtree(cache, ignore_errors=True)

    def knowledge_source_signature(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
    ) -> str | None:
        """Fingerprint allowlisted Knowledge sources for debounce and audit.

        Returns:
        - a content fingerprint when allowlisted sources exist
        - ``EMPTY_KNOWLEDGE_FINGERPRINT`` when the walk succeeds with zero sources
        - ``None`` when the walk is incomplete or the scope is unavailable
        """
        scope = self.resolve_scope(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind="knowledge",
            area_id=None,
            create_output=False,
        )
        cache = Path(tempfile.mkdtemp(prefix=".proxima-kfp-"))
        try:
            rel_paths, errors = _select_graphify_sources(
                scope.root,
                "knowledge",
                cache,
                scope.excluded_roots,
            )
            if errors:
                return None
            if not rel_paths:
                return EMPTY_KNOWLEDGE_FINGERPRINT
            return _fingerprint_files(
                scope.root,
                rel_paths,
                scope.excluded_roots,
            )
        finally:
            shutil.rmtree(cache, ignore_errors=True)

    def knowledge_source_marker(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
    ) -> str | None:
        scope = self.resolve_scope(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind="knowledge",
            area_id=None,
            create_output=False,
            deep_ops_scan=False,
        )
        rel_paths, errors = _select_knowledge_sources(
            scope.root,
            scope.excluded_roots,
        )
        if errors:
            return None
        digest = hashlib.sha256()
        for rel_text in rel_paths:
            try:
                rel, source = _resolve_source(
                    scope.root,
                    rel_text,
                    scope.excluded_roots,
                )
                stat = source.stat()
            except (GraphContextError, OSError):
                return None
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _owned_container(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
    ) -> dict[str, Any]:
        row = self._db_factory().execute(
            "SELECT * FROM projects "
            "WHERE slug = ? AND owner_user_id = ? AND archived_at IS NULL",
            (container_slug, owner_user_id),
        ).fetchone()
        if row is None:
            raise GraphScopeError("Container is not available")
        return dict(row)

    def resolve_scope(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        kind: str,
        area_id: int | None = None,
        create_output: bool = False,
        deep_ops_scan: bool = True,
    ) -> GraphScope:
        if kind not in GRAPH_KINDS:
            raise GraphScopeError("unknown graph kind")
        container = self._owned_container(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
        )
        try:
            if kind == "knowledge":
                if area_id is not None:
                    raise GraphScopeError(
                        "Knowledge graph scope cannot include an Area id"
                    )
                root = container_registry.ops_root(
                    self._db_factory(),
                    container,
                    deep_ops_scan=deep_ops_scan,
                )
                rel_path = None
                resolved_area_id = None
                # Fail closed when legacy Ops (rel_path='.') still shares the
                # Container root with registered Code Areas - allowlisted-looking
                # repo files must not enter Knowledge.
                ops_row = self._db_factory().execute(
                    "SELECT rel_path FROM project_areas "
                    "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded' "
                    "ORDER BY id LIMIT 1",
                    (container["id"],),
                ).fetchone()
                ops_rel = (
                    str(ops_row["rel_path"] or "").strip() if ops_row is not None else ""
                )
                code_area_ids = [
                    int(row["id"])
                    for row in self._db_factory().execute(
                        "SELECT id FROM project_areas "
                        "WHERE project_id = ? AND kind = 'code' "
                        "AND source != 'excluded'",
                        (container["id"],),
                    ).fetchall()
                ]
                if ops_rel in {"", "."} and code_area_ids:
                    raise GraphScopeError(
                        "Knowledge graph requires physical Ops separation from "
                        "Code Areas; legacy Ops root still overlaps Code scope"
                    )
                excluded_roots: tuple[Path, ...] = ()
                if code_area_ids:
                    try:
                        area_roots = container_registry.validated_area_roots(
                            self._db_factory(),
                            container,
                        )
                    except container_registry.ContainerBoundaryError:
                        area_roots = {}
                    resolved_ops = root.resolve(strict=True)
                    excluded_list: list[Path] = []
                    for code_id in code_area_ids:
                        candidate = area_roots.get(code_id)
                        if candidate is None:
                            continue
                        try:
                            resolved_code = candidate.resolve(strict=True)
                        except (OSError, RuntimeError):
                            continue
                        if (
                            resolved_code == resolved_ops
                            or _contains(resolved_ops, resolved_code)
                        ):
                            excluded_list.append(resolved_code)
                    excluded_roots = tuple(excluded_list)
            else:
                if area_id is None:
                    raise GraphScopeError("Code graph scope requires an Area id")
                row = self._db_factory().execute(
                    "SELECT id, rel_path FROM project_areas "
                    "WHERE id = ? AND project_id = ? AND kind = 'code' "
                    "AND source != 'excluded'",
                    (area_id, container["id"]),
                ).fetchone()
                if row is None:
                    raise GraphScopeError(
                        "Code graph Area is not in the selected Container"
                    )
                roots = container_registry.validated_area_roots(
                    self._db_factory(),
                    container,
                )
                root = roots[int(row["id"])]
                excluded_roots = tuple(
                    candidate
                    for candidate_id, candidate in roots.items()
                    if candidate_id != int(row["id"])
                    and candidate != root
                    and _contains(root, candidate)
                )
                rel_path = str(row["rel_path"])
                resolved_area_id = int(row["id"])
        except container_registry.ContainerBoundaryError as exc:
            raise GraphScopeError(str(exc)) from exc
        resolved_root = root.resolve(strict=True)
        workspace = self.config.get("workspace_root")
        workspace_root = Path(str(workspace)) if workspace else None
        if _is_worktree_path(resolved_root, workspace_root):
            raise GraphScopeError(
                "Task worktree graphs cannot be promoted as canonical"
            )
        return GraphScope(
            container_id=int(container["id"]),
            container_slug=str(container["slug"]),
            kind=kind,
            area_id=resolved_area_id,
            area_rel_path=rel_path,
            root=resolved_root,
            graph_path=_graph_path(resolved_root, create=create_output),
            excluded_roots=excluded_roots,
        )

    def _state_row(self, scope: GraphScope) -> sqlite3.Row:
        conn = self._db_factory()
        if scope.kind == "knowledge":
            row = conn.execute(
                "SELECT * FROM graph_states "
                "WHERE container_id = ? AND kind = 'knowledge' AND area_id IS NULL",
                (scope.container_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM graph_states "
                "WHERE container_id = ? AND kind = 'code' AND area_id = ?",
                (scope.container_id, scope.area_id),
            ).fetchone()
        if row is None:
            cursor = conn.execute(
                "INSERT INTO graph_states("
                "container_id, area_id, kind, root_path, graph_path, tool_version"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scope.container_id,
                    scope.area_id,
                    scope.kind,
                    str(scope.root),
                    str(scope.graph_path),
                    _installed_graphify_version(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM graph_states WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        elif (
            row["root_path"] != str(scope.root)
            or row["graph_path"] != str(scope.graph_path)
        ):
            next_state = "stale" if int(row["generation"]) > 0 else "missing"
            conn.execute(
                "UPDATE graph_states SET root_path = ?, graph_path = ?, state = ?, "
                "source_fingerprint = NULL, graph_sha256 = NULL, generation = 0, "
                "last_success_at = NULL, last_error = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (
                    str(scope.root),
                    str(scope.graph_path),
                    next_state,
                    "registered graph scope changed",
                    row["id"],
                ),
            )
            row = conn.execute(
                "SELECT * FROM graph_states WHERE id = ?",
                (row["id"],),
            ).fetchone()
        assert row is not None
        return row

    def _safe_error(self, scope: GraphScope, error: Any) -> str:
        text = str(error or "graph operation failed")
        for spelling in {str(scope.root), scope.root.as_posix()}:
            text = text.replace(spelling, "<scope>")
        return text[:_MAX_ERROR_CHARS]

    def _freshness(self, row: GraphStateRow) -> dict[str, Any]:
        payload = {
            "state": row["state"],
            "generation": int(row["generation"]),
            "source_fingerprint": row["source_fingerprint"],
            "graph_sha256": row["graph_sha256"],
            "tool_version": row["tool_version"],
            "semantic_backend": row["semantic_backend"],
            "last_success_at": row["last_success_at"],
            "last_attempt_at": row["last_attempt_at"],
            "last_error": row["last_error"],
        }
        # Lifecycle columns are optional until migration 36; omit when absent.
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        if "repo_head" in keys:
            payload["repo_head"] = row["repo_head"]
        if "rebuild_reason" in keys:
            payload["rebuild_reason"] = row["rebuild_reason"]
        return payload

    def _payload(
        self,
        scope: GraphScope,
        row: GraphStateRow,
    ) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "scope": scope.public(),
            "state": row["state"],
            "generation": int(row["generation"]),
            "freshness": self._freshness(row),
        }

    def _emit(self, scope: GraphScope, row: GraphStateRow) -> None:
        try:
            if row["state"] not in GRAPH_STATES:
                return
            session = self._db_factory().execute(
                "SELECT id FROM sessions WHERE mode = 'master' "
                "AND owner_user_id = ("
                "  SELECT owner_user_id FROM projects WHERE id = ?"
                ") ORDER BY id LIMIT 1",
                (scope.container_id,),
            ).fetchone()
            if session is None:
                return
            payload = self._payload(scope, row)
            payload["graph_state_id"] = payload.pop("id")
            cursor = self._db_factory().execute(
                "INSERT INTO events("
                "run_id, session_id, project_id, seq, type, payload"
                ") VALUES (NULL, ?, ?, ?, ?, ?)",
                (
                    int(session["id"]),
                    scope.container_id,
                    int(row["id"]),
                    f"graph.state.{row['state']}",
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            )
            event_id = cursor.lastrowid
            if event_id is None:
                raise RuntimeError("graph state event insert returned no id")
            payload["event_id"] = int(event_id)
            self._db_factory().execute(
                "UPDATE events SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    event_id,
                ),
            )
            self.app.state.hub.notify(int(session["id"]))
        except Exception:
            log.exception(
                "graph state event emission failed for graph state %s",
                row["id"],
            )

    def _transition(
        self,
        scope: GraphScope,
        state_id: int,
        state: str,
        *,
        error: str | None = None,
    ) -> sqlite3.Row:
        if state not in GRAPH_STATES:
            raise ValueError("unknown graph state")
        with self.app.state.db_lock:
            self._db_factory().execute(
                "UPDATE graph_states SET state = ?, last_error = ?, "
                "last_attempt_at = CASE "
                "  WHEN ? IN ('building', 'missing', 'failed') THEN ? "
                "  ELSE last_attempt_at END, "
                "tool_version = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    state,
                    error,
                    state,
                    iso_now(),
                    _installed_graphify_version(),
                    state_id,
                ),
            )
            row = self._db_factory().execute(
                "SELECT * FROM graph_states WHERE id = ?",
                (state_id,),
            ).fetchone()
        assert row is not None
        self._emit(scope, row)
        return row

    def _fail_or_requeue_rebuild(
        self,
        scope: GraphScope,
        state_id: int,
        *,
        error: str,
    ) -> sqlite3.Row:
        """Mark failed, or re-queue when a newer rebuild intent arrived mid-build."""
        with self.app.state.db_lock:
            columns = {
                row[1]
                for row in self._db_factory().execute(
                    "PRAGMA table_info(graph_states)"
                ).fetchall()
            }
            current = self._db_factory().execute(
                "SELECT * FROM graph_states WHERE id = ?",
                (state_id,),
            ).fetchone()
            assert current is not None
            follow_up = bool(
                str(current["state"] or "") == "building"
                and "rebuild_reason" in columns
                and current["rebuild_reason"]
            )
            if follow_up:
                self._db_factory().execute(
                    "UPDATE graph_states SET state = 'queued', last_error = ?, "
                    "last_attempt_at = ?, tool_version = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (
                        error,
                        iso_now(),
                        _installed_graphify_version(),
                        state_id,
                    ),
                )
            else:
                self._db_factory().execute(
                    "UPDATE graph_states SET state = 'failed', last_error = ?, "
                    "last_attempt_at = ?, tool_version = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (
                        error,
                        iso_now(),
                        _installed_graphify_version(),
                        state_id,
                    ),
                )
            row = self._db_factory().execute(
                "SELECT * FROM graph_states WHERE id = ?",
                (state_id,),
            ).fetchone()
        assert row is not None
        self._emit(scope, row)
        return row

    def list_states(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
    ) -> list[dict[str, Any]]:
        container = self._owned_container(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
        )
        scopes = [
            self.resolve_scope(
                owner_user_id=owner_user_id,
                container_slug=container_slug,
                kind="knowledge",
            )
        ]
        rows = self._db_factory().execute(
            "SELECT id FROM project_areas "
            "WHERE project_id = ? AND kind = 'code' AND source != 'excluded' "
            "ORDER BY rel_path, id",
            (container["id"],),
        ).fetchall()
        scopes.extend(
            self.resolve_scope(
                owner_user_id=owner_user_id,
                container_slug=container_slug,
                kind="code",
                area_id=int(row["id"]),
            )
            for row in rows
        )
        return [self._payload(scope, self._state_row(scope)) for scope in scopes]

    def _run_builder(
        self,
        *,
        scope: GraphScope,
        stage: Path,
        metadata: dict[str, Any],
        timeout_seconds: int,
        mode: str = "full",
        base_graph: Path | None = None,
        changed_rel_paths: list[str] | None = None,
        deleted_rel_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_build_graphify_worker,
            kwargs={
                "result_queue": result_queue,
                "root_text": str(scope.root),
                "stage_text": str(stage),
                "kind": scope.kind,
                "metadata": metadata,
                "excluded_root_texts": [
                    str(path) for path in scope.excluded_roots
                ],
                "mode": mode,
                "base_graph_text": str(base_graph) if base_graph else None,
                "changed_rel_paths": list(changed_rel_paths or []),
                "deleted_rel_paths": list(deleted_rel_paths or []),
            },
        )
        process.start()
        process.join(timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            raise GraphBuildTimeout("Graphify build exceeded its time budget")
        try:
            result = result_queue.get(timeout=1)
        except queue.Empty as exc:
            raise GraphBuildError(
                f"Graphify build process exited without a result ({process.exitcode})"
            ) from exc
        finally:
            result_queue.close()
        if not result.get("ok"):
            error_type = (
                GraphValidationError
                if result.get("code") == GraphValidationError.code
                else GraphBuildError
            )
            raise error_type(str(result.get("error") or "Graphify build failed"))
        return result

    def _current_sources(
        self,
        scope: GraphScope,
        cache_root: Path,
    ) -> tuple[list[str], str]:
        rel_paths, errors = _select_graphify_sources(
            scope.root,
            scope.kind,
            cache_root,
            scope.excluded_roots,
        )
        if errors:
            raise GraphValidationError(
                "scope could not be completely re-enumerated after build"
            )
        return rel_paths, _fingerprint_files(
            scope.root,
            rel_paths,
            scope.excluded_roots,
        )

    def _publish_generation(
        self,
        *,
        scope: GraphScope,
        state_row: GraphStateRow,
        stage: Path,
        build_result: Mapping[str, Any],
        expected_metadata: Mapping[str, Any],
    ) -> sqlite3.Row:
        current_graph_path = _graph_path(scope.root, create=True)
        if current_graph_path != scope.graph_path:
            raise GraphValidationError("registered graph output scope changed")
        try:
            stage_resolved = stage.resolve(strict=True)
            output_resolved = scope.graph_path.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise GraphValidationError(
                "temporary graph generation is unavailable"
            ) from exc
        if (
            not _contains(output_resolved, stage_resolved)
            or stage_resolved.parent == output_resolved
        ):
            raise GraphValidationError(
                "temporary graph generation escaped its output directory"
            )
        max_bytes = max(1024, int(self.config.get("graph_max_bytes", 0)))
        _, graph_sha256 = _validate_graph_data(
            stage,
            root=scope.root,
            expected_metadata=expected_metadata,
            max_bytes=max_bytes,
            excluded_roots=scope.excluded_roots,
        )
        current_files, current_fingerprint = self._current_sources(
            scope,
            stage.parent,
        )
        if (
            current_files != list(build_result["source_files"])
            or current_fingerprint != build_result["source_fingerprint"]
        ):
            raise GraphValidationError(
                "graph scope changed during generation; canonical graph was preserved"
            )

        canonical = scope.graph_path
        last_good = canonical.with_name("graph.last-good.json")
        prior_bytes: bytes | None = None
        if canonical.exists():
            if canonical.is_symlink() or not canonical.is_file():
                raise GraphValidationError("canonical graph is not a regular file")
            prior_bytes = canonical.read_bytes()
            _write_bytes_fsync(last_good, prior_bytes)
        os.replace(stage, canonical)
        directory_fd = os.open(canonical.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        now = iso_now()
        repo_head = None
        if scope.kind == "code":
            try:
                repo_head = self.repo_head_sha(scope.root)
            except GraphScopeError:
                repo_head = None
        try:
            with self.app.state.db_lock:
                columns = {
                    row[1]
                    for row in self._db_factory().execute(
                        "PRAGMA table_info(graph_states)"
                    ).fetchall()
                }
                current = self._db_factory().execute(
                    "SELECT * FROM graph_states WHERE id = ?",
                    (state_row["id"],),
                ).fetchone()
                assert current is not None
                follow_up = bool(
                    str(current["state"] or "") == "building"
                    and "rebuild_reason" in columns
                    and current["rebuild_reason"]
                )
                next_state = "queued" if follow_up else "fresh"
                backend = str(
                    expected_metadata.get("semantic_backend") or SEMANTIC_BACKEND_LOCAL
                )
                if "repo_head" in columns:
                    if follow_up:
                        self._db_factory().execute(
                            "UPDATE graph_states SET state = ?, generation = ?, "
                            "source_fingerprint = ?, graph_sha256 = ?, tool_version = ?, "
                            "semantic_backend = ?, last_success_at = ?, "
                            "last_attempt_at = ?, last_error = NULL, "
                            "repo_head = ?, "
                            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (
                                next_state,
                                expected_metadata["generation"],
                                expected_metadata["source_fingerprint"],
                                graph_sha256,
                                GRAPHIFY_VERSION,
                                backend,
                                now,
                                now,
                                repo_head,
                                state_row["id"],
                            ),
                        )
                    else:
                        self._db_factory().execute(
                            "UPDATE graph_states SET state = 'fresh', generation = ?, "
                            "source_fingerprint = ?, graph_sha256 = ?, tool_version = ?, "
                            "semantic_backend = ?, last_success_at = ?, "
                            "last_attempt_at = ?, last_error = NULL, "
                            "repo_head = ?, pending_base_commit = NULL, "
                            "pending_head_commit = NULL, rebuild_reason = NULL, "
                            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (
                                expected_metadata["generation"],
                                expected_metadata["source_fingerprint"],
                                graph_sha256,
                                GRAPHIFY_VERSION,
                                backend,
                                now,
                                now,
                                repo_head,
                                state_row["id"],
                            ),
                        )
                else:
                    self._db_factory().execute(
                        "UPDATE graph_states SET state = 'fresh', generation = ?, "
                        "source_fingerprint = ?, graph_sha256 = ?, tool_version = ?, "
                        "semantic_backend = ?, last_success_at = ?, "
                        "last_attempt_at = ?, last_error = NULL, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (
                            expected_metadata["generation"],
                            expected_metadata["source_fingerprint"],
                            graph_sha256,
                            GRAPHIFY_VERSION,
                            backend,
                            now,
                            now,
                            state_row["id"],
                        ),
                    )
                row = self._db_factory().execute(
                    "SELECT * FROM graph_states WHERE id = ?",
                    (state_row["id"],),
                ).fetchone()
        except Exception:
            if prior_bytes is None:
                canonical.unlink(missing_ok=True)
            else:
                _write_bytes_fsync(canonical, prior_bytes)
            raise
        assert row is not None
        self._emit(scope, row)
        return row

    def enqueue_code_rebuild(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        area_id: int,
        reason: str,
        mode: str = "full",
        mark_stale: bool = False,
        pending_base_commit: str | None = None,
        pending_head_commit: str | None = None,
    ) -> dict[str, Any]:
        """Create state if needed, optionally mark stale, and queue a Code rebuild."""
        return self._enqueue_rebuild(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind="code",
            area_id=area_id,
            reason=reason,
            mode=mode,
            mark_stale=mark_stale,
            pending_base_commit=pending_base_commit,
            pending_head_commit=pending_head_commit,
        )

    def enqueue_knowledge_rebuild(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        reason: str,
        mark_stale: bool = False,
    ) -> dict[str, Any]:
        """Create Knowledge state if needed and queue a full Ops rebuild."""
        return self._enqueue_rebuild(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind="knowledge",
            area_id=None,
            reason=reason,
            mode="full",
            mark_stale=mark_stale,
        )

    def _enqueue_rebuild(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        kind: str,
        area_id: int | None,
        reason: str,
        mode: str = "full",
        mark_stale: bool = False,
        pending_base_commit: str | None = None,
        pending_head_commit: str | None = None,
    ) -> dict[str, Any]:
        """Create state if needed, optionally mark stale, and queue a rebuild.

        When a build is already in flight, persist rebuild intent (reason +
        pending commit range) without clobbering ``building``. Publish/fail
        re-queues when that follow-up intent is still present.
        """
        del mode  # selection happens at drain time from pending commits + reason
        del mark_stale  # queued (or building+intent) is the durable stale marker
        scope = self.resolve_scope(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind=kind,
            area_id=area_id,
            create_output=True,
        )
        columns = {
            row[1]
            for row in self._db_factory().execute(
                "PRAGMA table_info(graph_states)"
            ).fetchall()
        }
        with self.app.state.db_lock:
            state_row = self._state_row(scope)
            state_id = int(state_row["id"])
            current_state = str(state_row["state"] or "")
            building = current_state == "building"
            if "rebuild_reason" in columns:
                try:
                    existing_base = state_row["pending_base_commit"]
                except (IndexError, KeyError, TypeError):
                    existing_base = None
                try:
                    existing_head = state_row["pending_head_commit"]
                except (IndexError, KeyError, TypeError):
                    existing_head = None
                try:
                    existing_reason = state_row["rebuild_reason"]
                except (IndexError, KeyError, TypeError):
                    existing_reason = None
                new_base, new_head = _coalesce_pending_range(
                    existing_base,
                    existing_head,
                    pending_base_commit,
                    pending_head_commit,
                )
                coalesced_reason = _coalesce_rebuild_reason(
                    existing_reason,
                    reason,
                )
                if building:
                    # Keep building; only record follow-up intent for after publish/fail.
                    self._db_factory().execute(
                        "UPDATE graph_states SET rebuild_reason = ?, "
                        "pending_base_commit = ?, pending_head_commit = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (coalesced_reason, new_base, new_head, state_id),
                    )
                else:
                    self._db_factory().execute(
                        "UPDATE graph_states SET state = 'queued', rebuild_reason = ?, "
                        "pending_base_commit = ?, pending_head_commit = ?, "
                        "last_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (coalesced_reason, new_base, new_head, state_id),
                    )
                row = self._db_factory().execute(
                    "SELECT * FROM graph_states WHERE id = ?",
                    (state_id,),
                ).fetchone()
            else:
                self._db_factory().execute(
                    "UPDATE graph_states SET state = 'queued', last_error = NULL, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (state_id,),
                )
                row = self._db_factory().execute(
                    "SELECT * FROM graph_states WHERE id = ?",
                    (state_id,),
                ).fetchone()
        assert row is not None
        self._emit(scope, row)
        return self._payload(scope, row)

    def rebuild(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        kind: str,
        area_id: int | None = None,
        mode: str = "full",
        pending_base_commit: str | None = None,
        pending_head_commit: str | None = None,
        rebuild_reason: str | None = None,
    ) -> dict[str, Any]:
        egress_enabled = bool(self.config.get("graph_semantic_egress_enabled"))
        backend = semantic_backend_label(
            kind=kind,
            egress_enabled=egress_enabled,
        )
        if rebuild_reason:
            log.info(
                "%s graph rebuild reason=%s container=%s area=%s semantic_backend=%s",
                kind,
                rebuild_reason,
                container_slug,
                area_id,
                backend,
            )
        scope = self.resolve_scope(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind=kind,
            area_id=area_id,
            create_output=True,
        )
        state_row = self._state_row(scope)
        lock = self._lock_for(scope)
        if not lock.acquire(blocking=False):
            raise GraphBuildError("this graph scope is already building")
        generation_dir: Path | None = None
        build_mode = "full"
        state_id = int(state_row["id"])
        with self._locks_guard:
            self._active_rebuild_ids.add(state_id)
        # Last-success tool pin comes from the published graph, not graph_states:
        # failed/queued/building transitions overwrite the DB column with the
        # installed pin and would otherwise bypass the full-rebuild guard.
        prior_tool_version = _published_graph_tool_version(scope.graph_path)
        try:
            self._transition(scope, state_id, "queued")
            installed = _installed_graphify_version()
            if installed is None:
                message = (
                    f"Graphify {GRAPHIFY_VERSION} is not installed in the API environment"
                )
                row = self._transition(
                    scope,
                    state_id,
                    "missing",
                    error=message,
                )
                return self._payload(scope, row)
            if installed != GRAPHIFY_VERSION:
                raise GraphBuildError(
                    f"Graphify version mismatch: expected {GRAPHIFY_VERSION}, got {installed}"
                )
            # Cloud semantic extraction never runs from credentials alone.
            # Even an explicit egress opt-in fails closed until a future policy
            # ships a real local-or-cloud adapter.
            if scope.kind == "knowledge" and egress_enabled:
                log.warning(
                    "knowledge graph rebuild refused: semantic egress opt-in is "
                    "set but cloud extraction is not implemented "
                    "(container=%s backend=%s)",
                    container_slug,
                    backend,
                )
                raise GraphBuildError(
                    "semantic model egress is not implemented; "
                    "Knowledge graphs remain local-structural only"
                )
            # Claim any queued intent before the building transition clears pending.
            claimed_head: str | None
            with self.app.state.db_lock:
                pre_build = self._db_factory().execute(
                    "SELECT * FROM graph_states WHERE id = ?",
                    (state_id,),
                ).fetchone()
                assert pre_build is not None
                try:
                    claimed_head = pre_build["pending_head_commit"]
                except (IndexError, KeyError, TypeError):
                    claimed_head = None
                columns = {
                    row[1]
                    for row in self._db_factory().execute(
                        "PRAGMA table_info(graph_states)"
                    ).fetchall()
                }
                if "rebuild_reason" in columns:
                    self._db_factory().execute(
                        "UPDATE graph_states SET state = 'building', last_error = NULL, "
                        "last_attempt_at = ?, tool_version = ?, "
                        "rebuild_reason = NULL, pending_base_commit = NULL, "
                        "pending_head_commit = NULL, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (iso_now(), installed, state_id),
                    )
                else:
                    self._db_factory().execute(
                        "UPDATE graph_states SET state = 'building', last_error = NULL, "
                        "last_attempt_at = ?, tool_version = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (iso_now(), installed, state_id),
                    )
                state_row = self._db_factory().execute(
                    "SELECT * FROM graph_states WHERE id = ?",
                    (state_id,),
                ).fetchone()
            assert state_row is not None
            self._emit(scope, state_row)
            pending_head_commit = claimed_head or pending_head_commit
            generation = int(state_row["generation"]) + 1
            if _graph_path(scope.root, create=True) != scope.graph_path:
                raise GraphValidationError("registered graph output scope changed")
            generation_dir = Path(
                tempfile.mkdtemp(
                    prefix=".proxima-generation-",
                    dir=scope.graph_path.parent,
                )
            )
            stage = generation_dir / "graph.json"
            placeholder_metadata = scope.metadata(
                generation,
                "",
                semantic_backend=backend,
            )
            timeout_seconds = min(
                600,
                max(
                    1,
                    int(self.config.get("graph_build_timeout_seconds", 120)),
                ),
            )

            changed: list[str] = []
            deleted: list[str] = []
            base_graph: Path | None = None
            incremental_head: str | None = None
            if (
                mode == "incremental"
                and scope.kind == "code"
                and scope.graph_path.is_file()
                and int(state_row["generation"] or 0) > 0
            ):
                tool_ok = prior_tool_version == GRAPHIFY_VERSION
                try:
                    base = state_row["repo_head"] or None
                except (IndexError, KeyError, TypeError):
                    base = None
                head = pending_head_commit or self.repo_head_sha(scope.root)
                live_head = self.repo_head_sha(scope.root)
                tree_stable = (
                    head is not None
                    and live_head is not None
                    and live_head == head
                    and self.tracked_dirty_signature(scope.root) is None
                    and self.incremental_sources_covered_by_commit(
                        scope,
                        str(head),
                    )
                )
                if tool_ok and base and head and tree_stable:
                    changed, deleted, unsafe = self.changed_files_between(
                        scope.root,
                        str(base),
                        str(head),
                    )
                    if unsafe is None and (changed or deleted):
                        build_mode = "incremental"
                        base_graph = scope.graph_path
                        incremental_head = str(head)

            build_result = self._run_builder(
                scope=scope,
                stage=stage,
                metadata=placeholder_metadata,
                timeout_seconds=timeout_seconds,
                mode=build_mode,
                base_graph=base_graph,
                changed_rel_paths=changed,
                deleted_rel_paths=deleted,
            )

            if build_mode == "incremental":
                live_head = self.repo_head_sha(scope.root)
                if (
                    incremental_head is None
                    or live_head is None
                    or live_head != incremental_head
                    or self.tracked_dirty_signature(scope.root) is not None
                    or not self.incremental_sources_covered_by_commit(
                        scope,
                        str(incremental_head),
                    )
                ):
                    raise GraphValidationError(
                        "live tree drifted during incremental rebuild; "
                        "fallback_full required"
                    )

            expected_metadata = scope.metadata(
                generation,
                str(build_result["source_fingerprint"]),
                semantic_backend=backend,
            )
            log.info(
                "%s graph published container=%s generation=%s semantic_backend=%s "
                "sources=%s",
                scope.kind,
                scope.container_slug,
                generation,
                backend,
                len(build_result.get("source_files") or []),
            )
            row = self._publish_generation(
                scope=scope,
                state_row=state_row,
                stage=stage,
                build_result=build_result,
                expected_metadata=expected_metadata,
            )
            return self._payload(scope, row)
        except GraphContextError as exc:
            error = self._safe_error(scope, exc)
            row = self._fail_or_requeue_rebuild(
                scope,
                state_id,
                error=error,
            )
            if build_mode == "incremental":
                raise GraphBuildError(
                    "incremental update failed; fallback_full required"
                ) from None
            if isinstance(exc, GraphBuildError):
                raise
            raise GraphBuildError(error) from exc
        except Exception as exc:
            error = self._safe_error(scope, exc)
            self._fail_or_requeue_rebuild(
                scope,
                state_id,
                error=error,
            )
            if build_mode == "incremental":
                raise GraphBuildError(
                    "incremental update failed; fallback_full required"
                ) from None
            raise GraphBuildError(error) from exc
        finally:
            if generation_dir is not None:
                shutil.rmtree(generation_dir, ignore_errors=True)
            with self._locks_guard:
                self._active_rebuild_ids.discard(state_id)
            lock.release()

    def _budgets(
        self,
        *,
        depth: int | None,
        timeout_ms: int | None,
        token_budget: int | None,
        result_limit: int | None,
    ) -> GraphQueryBudgets:
        config = self.config
        ceilings = {
            "depth": min(6, max(1, int(config.get("graph_query_max_depth", 4)))),
            "timeout_ms": min(
                30_000,
                max(100, int(config.get("graph_query_timeout_ms", 3000))),
            ),
            "token_budget": min(
                16_000,
                max(256, int(config.get("graph_query_token_budget", 2000))),
            ),
            "result_limit": min(
                200,
                max(1, int(config.get("graph_query_result_limit", 40))),
            ),
        }
        def clamp(value: int | None, ceiling: int, floor: int) -> int:
            requested = ceiling if value is None else int(value)
            return min(ceiling, max(floor, requested))

        return GraphQueryBudgets(
            depth=clamp(depth, ceilings["depth"], 1),
            timeout_ms=clamp(timeout_ms, ceilings["timeout_ms"], 100),
            token_budget=clamp(
                token_budget,
                ceilings["token_budget"],
                256,
            ),
            result_limit=clamp(
                result_limit,
                ceilings["result_limit"],
                1,
            ),
        )

    def _unavailable_result(
        self,
        *,
        scope: GraphScope,
        row: GraphStateRow,
        budgets: GraphQueryBudgets,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "scope": scope.public(),
            "generation": int(row["generation"]),
            "freshness": self._freshness(row),
            "limits": {
                "depth": budgets.depth,
                "timeout_ms": budgets.timeout_ms,
                "token_budget": budgets.token_budget,
                "result_limit": budgets.result_limit,
            },
            "items": [],
            "citations": [],
            "provenance": [],
            "truncated": False,
            "elapsed_ms": 0,
            "error": {"code": code, "message": message},
        }

    def query(
        self,
        *,
        owner_user_id: int,
        container_slug: str,
        kind: str,
        question: str,
        area_id: int | None = None,
        depth: int | None = None,
        timeout_ms: int | None = None,
        token_budget: int | None = None,
        result_limit: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(question, str) or not question.strip():
            raise GraphContextError("graph query is required")
        if len(question) > 4000:
            raise GraphContextError("graph query exceeds the input budget")
        scope = self.resolve_scope(
            owner_user_id=owner_user_id,
            container_slug=container_slug,
            kind=kind,
            area_id=area_id,
        )
        row = self._state_row(scope)
        budgets = self._budgets(
            depth=depth,
            timeout_ms=timeout_ms,
            token_budget=token_budget,
            result_limit=result_limit,
        )
        if (
            int(row["generation"]) <= 0
            or _graph_path(scope.root, create=False) != scope.graph_path
            or not scope.graph_path.is_file()
        ):
            return self._unavailable_result(
                scope=scope,
                row=row,
                budgets=budgets,
                code="graph_missing",
                message="No validated graph generation is available.",
            )

        # Integrity: refuse tampered canonical bytes before any graph walk.
        expected_sha = str(row["graph_sha256"] or "").strip().lower()
        if not expected_sha:
            return self._unavailable_result(
                scope=scope,
                row=row,
                budgets=budgets,
                code="graph_tampered",
                message="Canonical graph has no published digest.",
            )
        try:
            actual_sha = hashlib.sha256(
                scope.graph_path.read_bytes()
            ).hexdigest()
        except OSError:
            return self._unavailable_result(
                scope=scope,
                row=row,
                budgets=budgets,
                code="graph_tampered",
                message="Canonical graph could not be verified.",
            )
        if actual_sha != expected_sha:
            return self._unavailable_result(
                scope=scope,
                row=row,
                budgets=budgets,
                code="graph_tampered",
                message="Canonical graph no longer matches its published digest.",
            )

        published_backend = str(
            row["semantic_backend"] or SEMANTIC_BACKEND_LOCAL
        )
        expected_metadata = scope.metadata(
            int(row["generation"]),
            str(row["source_fingerprint"]),
            semantic_backend=published_backend,
        )
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_query_worker,
            args=(
                result_queue,
                {
                    "path": str(scope.graph_path),
                    "root": str(scope.root),
                    "expected_metadata": expected_metadata,
                    "expected_sha256": expected_sha,
                    "max_bytes": max(
                        1024,
                        int(self.config.get("graph_max_bytes", 0)),
                    ),
                    "question": question.strip(),
                    "budgets": {
                        "depth": budgets.depth,
                        "timeout_ms": budgets.timeout_ms,
                        "token_budget": budgets.token_budget,
                        "result_limit": budgets.result_limit,
                    },
                    "scope": scope.public(),
                    "freshness": self._freshness(row),
                    "excluded_roots": [
                        str(path) for path in scope.excluded_roots
                    ],
                },
            ),
        )
        process.start()
        process.join(budgets.timeout_ms / 1000)
        if process.is_alive():
            process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            result_queue.close()
            return self._unavailable_result(
                scope=scope,
                row=row,
                budgets=budgets,
                code=GraphQueryTimeout.code,
                message="Graph query exceeded its server-owned time budget.",
            )
        try:
            outcome = result_queue.get(timeout=1)
        except queue.Empty:
            outcome = {
                "ok": False,
                "code": "graph_query_failed",
                "error": f"query process exited without a result ({process.exitcode})",
            }
        finally:
            result_queue.close()
        if outcome.get("ok"):
            result = dict(outcome["result"])
            if scope.kind == "knowledge" and not self._knowledge_result_allowed(
                result
            ):
                return self._unavailable_result(
                    scope=scope,
                    row=row,
                    budgets=budgets,
                    code="graph_tampered",
                    message=(
                        "Knowledge query cited paths outside the Ops allowlist"
                    ),
                )
            return result
        return self._unavailable_result(
            scope=scope,
            row=row,
            budgets=budgets,
            code=str(outcome.get("code") or "graph_query_failed"),
            message=self._safe_error(
                scope,
                outcome.get("error") or "Graph query failed.",
            ),
        )

    @staticmethod
    def _knowledge_result_allowed(result: Mapping[str, Any]) -> bool:
        """Refuse Knowledge results that cite non-allowlisted Ops paths."""
        paths: list[str] = []
        for citation in result.get("citations") or []:
            if isinstance(citation, Mapping) and citation.get("path"):
                paths.append(str(citation["path"]))
        for item in result.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            for citation in item.get("citations") or []:
                if isinstance(citation, Mapping) and citation.get("path"):
                    paths.append(str(citation["path"]))
            source = item.get("source_file") or item.get("path")
            if source:
                paths.append(str(source))
        for path in paths:
            normalized = path.replace("\\", "/").strip().strip("/")
            if not normalized:
                continue
            if not _knowledge_path_allowed(normalized):
                return False
        return True
