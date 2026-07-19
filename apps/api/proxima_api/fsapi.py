from __future__ import annotations

import os
import shutil
from pathlib import Path

MAX_READ_BYTES = 1_000_000
REFERENCE_MAX_SCANNED = 20_000
REFERENCE_MAX_DEPTH = 12
REFERENCE_MAX_RESULTS = 2_000

# Autocomplete should surface owner-authored project files, not dependency trees,
# generated caches, or credentials that happen to live beside the source.  These
# names are deliberately conservative: a hidden/secret file can still be opened by
# an explicitly typed path, but it is never advertised by the @-reference picker.
_REFERENCE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".cache",
    ".turbo",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "vendor",
    "venv",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    "__pycache__",
}
_REFERENCE_SECRET_NAMES = {
    ".env",
    ".envrc",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "auth.json",
    ".credentials.json",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "id_rsa",
    "id_ed25519",
}
_REFERENCE_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


class FsError(Exception):
    """Raised for any disallowed or invalid filesystem operation."""


def resolve_in_project(root: Path, rel: str) -> Path:
    """Resolve rel against the project root, jailed inside it.

    Rejects absolute paths and any path that escapes the project root
    (including via .. or symlinks).
    """
    root = Path(root).resolve()
    rel = (rel or "").strip()
    if "\x00" in rel:
        raise FsError("invalid path")
    # Reject absolute paths explicitly before any joining
    if rel and Path(rel).is_absolute():
        raise FsError("path escapes project root")
    rel = rel.lstrip("/")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise FsError("path escapes project root")
    return target


def list_tree(root: Path, rel: str) -> list[dict]:
    target = resolve_in_project(root, rel)
    if not target.is_dir():
        raise FsError("not a directory")
    entries: list[dict] = []
    for child in target.iterdir():
        is_dir = child.is_dir()
        try:
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            size = 0
        entries.append({
            "name": child.name,
            "type": "dir" if is_dir else "file",
            "size": size,
        })
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return entries


def _is_reference_secret(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in _REFERENCE_SECRET_NAMES
        or lowered.startswith(".env.")
        or lowered.endswith(_REFERENCE_SECRET_SUFFIXES)
    )


def list_reference_files(
    root: Path,
    *,
    limit: int = REFERENCE_MAX_RESULTS,
    max_scanned: int = REFERENCE_MAX_SCANNED,
    max_depth: int = REFERENCE_MAX_DEPTH,
) -> tuple[list[dict[str, str]], bool]:
    """Return safe, project-relative files for the @-reference autocomplete.

    Only paths are exposed; file contents and metadata never leave this boundary.
    Traversal is bounded independently by inspected entries, nesting depth, and
    returned results.  Symlinks are skipped instead of followed so a linked project
    cannot advertise files outside its authorized root.
    """
    base = Path(root).resolve()
    if not base.is_dir():
        return [], False

    safe_limit = max(1, min(int(limit), REFERENCE_MAX_RESULTS))
    safe_scanned = max(1, min(int(max_scanned), REFERENCE_MAX_SCANNED))
    safe_depth = max(0, min(int(max_depth), REFERENCE_MAX_DEPTH))
    files: list[dict[str, str]] = []
    scanned = 0
    truncated = False
    stopped = False

    def visit(directory: Path, depth: int) -> None:
        nonlocal scanned, truncated, stopped
        if stopped:
            return
        try:
            entries = os.scandir(directory)
        except OSError:
            return
        with entries:
            # os.scandir order is filesystem-dependent. Sort each directory before
            # applying caps so the same project produces a stable autocomplete list.
            ordered_entries = sorted(entries, key=lambda entry: entry.name.casefold())
            for entry in ordered_entries:
                if stopped:
                    return
                if scanned >= safe_scanned:
                    truncated = True
                    stopped = True
                    return
                scanned += 1
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        lowered = entry.name.casefold()
                        if entry.name.startswith(".") or lowered in _REFERENCE_SKIP_DIRS:
                            continue
                        if depth >= safe_depth:
                            truncated = True
                            continue
                        visit(Path(entry.path), depth + 1)
                        continue
                    if not entry.is_file(follow_symlinks=False) or _is_reference_secret(entry.name):
                        continue
                except OSError:
                    continue
                if len(files) >= safe_limit:
                    truncated = True
                    stopped = True
                    return
                try:
                    rel = Path(entry.path).relative_to(base).as_posix()
                except ValueError:
                    # Defensive only: no-follow traversal should already guarantee it.
                    continue
                files.append({"path": rel})

    visit(base, 0)
    files.sort(key=lambda item: item["path"].casefold())
    return files, truncated


def walk_files(root: Path, rel: str = "", limit: int = MAX_READ_BYTES) -> list[dict]:
    """Return all readable text files (path relative to base, content) under rel.

    Recursive; skips symlinks, oversized files, and non-UTF-8 files. Used to
    bulk-load a wiki for graph/search/backlink building.
    """
    base = resolve_in_project(root, rel)
    out: list[dict] = []
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        try:
            if p.stat().st_size > limit:
                continue
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.append({"path": str(p.relative_to(base)), "content": text})
    return out


def read_file(root: Path, rel: str) -> str:
    target = resolve_in_project(root, rel)
    if not target.is_file():
        raise FsError("not a file")
    if target.stat().st_size > MAX_READ_BYTES:
        raise FsError("file too large to open")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FsError("binary file not supported") from exc
    except OSError as exc:
        raise FsError(f"cannot read file: {exc.strerror}") from exc


def write_file(root: Path, rel: str, content: str) -> None:
    target = resolve_in_project(root, rel)
    if target.is_dir():
        raise FsError("target is a directory")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise FsError(f"cannot write file: {exc.strerror}") from exc


def mkdir(root: Path, rel: str) -> None:
    target = resolve_in_project(root, rel)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FsError(f"cannot create directory: {exc.strerror}") from exc


def rename(root: Path, src_rel: str, dst_rel: str) -> None:
    src = resolve_in_project(root, src_rel)
    dst = resolve_in_project(root, dst_rel)
    if not src.exists():
        raise FsError("source does not exist")
    # Refuse to clobber an existing destination — Path.rename overwrites silently
    # on POSIX, which would destroy the target file's contents with no warning.
    if dst.exists() and dst != src:
        raise FsError("a file or folder with that name already exists")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        raise FsError(f"rename failed: {exc.strerror}") from exc


def delete(root: Path, rel: str) -> None:
    target = resolve_in_project(root, rel)
    if target == Path(root).resolve():
        raise FsError("cannot delete project root")
    if not target.exists():
        raise FsError("path does not exist")
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        raise FsError(f"delete failed: {exc.strerror}") from exc
