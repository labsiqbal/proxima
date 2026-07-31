from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class DirectoryBrowseUnavailable(Exception):
    pass


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolved_roots(configured_roots: Iterable[str | Path]) -> list[Path]:
    roots: list[Path] = []
    for configured in configured_roots:
        try:
            root = Path(configured).expanduser().resolve()
        except OSError:
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _candidates(requested: str, roots: list[Path]) -> list[tuple[Path, Path]]:
    if requested:
        try:
            selected = Path(requested).expanduser().resolve()
        except OSError:
            selected = None
        if selected is not None:
            owner = next((root for root in roots if _inside(selected, root)), None)
            if owner is not None:
                candidates: list[tuple[Path, Path]] = []
                current = selected
                while True:
                    candidates.append((current, owner))
                    if current == owner:
                        return candidates
                    current = current.parent
    return [(root, root) for root in roots]


def browse_directory(requested: str, configured_roots: Iterable[str | Path]) -> dict[str, object]:
    roots = _resolved_roots(configured_roots)
    if not roots:
        raise DirectoryBrowseUnavailable("No readable folder is available inside the allowed roots")

    for candidate, root in _candidates(requested, roots):
        try:
            if not candidate.is_dir():
                continue
            entries = list(candidate.iterdir())
        except OSError:
            continue

        dirs: list[dict[str, str]] = []
        for child in sorted(entries, key=lambda entry: entry.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                resolved = child.resolve()
                if child.is_dir() and _inside(resolved, root):
                    dirs.append({"name": child.name, "path": str(child)})
            except OSError:
                continue

        parent = (
            str(candidate.parent)
            if candidate != root and _inside(candidate.parent, root)
            else None
        )
        return {
            "path": str(candidate),
            "parent": parent,
            "dirs": dirs,
            "roots": [str(item) for item in roots],
        }

    raise DirectoryBrowseUnavailable("No readable folder is available inside the allowed roots")
