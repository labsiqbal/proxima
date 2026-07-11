from __future__ import annotations

import shutil
from pathlib import Path

MAX_READ_BYTES = 1_000_000


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
