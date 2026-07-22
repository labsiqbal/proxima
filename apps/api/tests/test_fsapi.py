from __future__ import annotations

import os

import pytest

from proxima_api import fsapi


def _project(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "sub" / "b.md").write_text("# title", encoding="utf-8")
    return root


def test_resolve_in_project_rejects_traversal(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(fsapi.FsError):
        fsapi.resolve_in_project(root, "../secret")
    with pytest.raises(fsapi.FsError):
        fsapi.resolve_in_project(root, "/etc/passwd")


def test_resolve_in_project_allows_inside(tmp_path):
    root = _project(tmp_path)
    assert fsapi.resolve_in_project(root, "sub/b.md") == (root / "sub" / "b.md").resolve()
    assert fsapi.resolve_in_project(root, "") == root.resolve()


def test_list_tree_returns_sorted_dirs_first(tmp_path):
    root = _project(tmp_path)
    entries = fsapi.list_tree(root, "")
    assert entries[0] == {"name": "sub", "type": "dir", "size": 0}
    assert {"name": "a.txt", "type": "file", "size": 5} in entries


def test_list_tree_missing_path_returns_empty(tmp_path):
    """Optional folders (design versions/assets) may not exist yet — list is empty, not an error."""
    root = _project(tmp_path)
    assert fsapi.list_tree(root, "artifacts/design/new_id/versions") == []
    assert fsapi.list_tree(root, "does-not-exist") == []


def test_list_tree_file_path_still_errors(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(fsapi.FsError, match="not a directory"):
        fsapi.list_tree(root, "a.txt")


def test_list_reference_files_returns_nested_path_only_entries(tmp_path):
    root = tmp_path / "proj"
    (root / "src" / "components").mkdir(parents=True)
    (root / "README.md").write_text("readme", encoding="utf-8")
    (root / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
    (root / "src" / "components" / "App.tsx").write_text("export {}", encoding="utf-8")

    files, truncated = fsapi.list_reference_files(root)

    assert files == [
        {"path": "README.md"},
        {"path": "src/components/App.tsx"},
        {"path": "src/main.py"},
    ]
    assert truncated is False


def test_list_reference_files_prunes_secrets_heavy_hidden_and_symlinks(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "public.md").write_text("safe", encoding="utf-8")
    for rel in (
        ".env",
        ".env.production",
        "auth.json",
        "credentials.json",
        "signing.key",
    ):
        (root / rel).write_text("secret", encoding="utf-8")
    for directory in ("node_modules", "build", ".hidden"):
        target = root / directory
        target.mkdir()
        (target / "ignored.js").write_text("ignored", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    os.symlink(outside / "outside.txt", root / "linked-file.txt")
    os.symlink(outside, root / "linked-dir")

    files, truncated = fsapi.list_reference_files(root)

    assert files == [{"path": "public.md"}]
    assert truncated is False


def test_list_reference_files_enforces_result_scan_and_depth_caps(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")

    limited, result_truncated = fsapi.list_reference_files(root, limit=2)
    scanned, scan_truncated = fsapi.list_reference_files(root, max_scanned=1)

    deep = root
    for index in range(3):
        deep = deep / f"level-{index}"
        deep.mkdir()
    (deep / "too-deep.txt").write_text("deep", encoding="utf-8")
    shallow_depth, depth_truncated = fsapi.list_reference_files(root, max_depth=1)

    assert len(limited) == 2 and result_truncated is True
    assert len(scanned) <= 1 and scan_truncated is True
    assert all(item["path"] != "level-0/level-1/level-2/too-deep.txt" for item in shallow_depth)
    assert depth_truncated is True


def test_read_and_write_file(tmp_path):
    root = _project(tmp_path)
    assert fsapi.read_file(root, "a.txt") == "hello"
    fsapi.write_file(root, "sub/c.txt", "new content")
    assert (root / "sub" / "c.txt").read_text(encoding="utf-8") == "new content"


def test_read_file_rejects_too_large(tmp_path):
    root = _project(tmp_path)
    (root / "big.txt").write_text("x" * (fsapi.MAX_READ_BYTES + 1), encoding="utf-8")
    with pytest.raises(fsapi.FsError):
        fsapi.read_file(root, "big.txt")


def test_mkdir_rename_delete(tmp_path):
    root = _project(tmp_path)
    fsapi.mkdir(root, "newdir")
    assert (root / "newdir").is_dir()
    fsapi.rename(root, "a.txt", "renamed.txt")
    assert (root / "renamed.txt").exists() and not (root / "a.txt").exists()
    # renaming onto an existing file must refuse (no silent overwrite / data loss)
    (root / "keep.txt").write_text("precious")
    (root / "other.txt").write_text("other")
    try:
        fsapi.rename(root, "other.txt", "keep.txt")
        assert False, "expected FsError on existing destination"
    except fsapi.FsError:
        pass
    assert (root / "keep.txt").read_text() == "precious"  # untouched
    assert (root / "other.txt").exists()                  # source untouched
    fsapi.delete(root, "renamed.txt")
    assert not (root / "renamed.txt").exists()
    fsapi.delete(root, "sub")
    assert not (root / "sub").exists()


def test_mkdir_collision_maps_to_fserror(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(fsapi.FsError, match="cannot create directory"):
        fsapi.mkdir(root, "a.txt")
    with pytest.raises(fsapi.FsError, match="cannot create directory"):
        fsapi.mkdir(root, "a.txt/child")


def test_rename_parent_collision_maps_to_fserror(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(fsapi.FsError, match="rename failed"):
        fsapi.rename(root, "a.txt", "sub/b.md/renamed.txt")
    assert (root / "a.txt").read_text(encoding="utf-8") == "hello"


def test_list_tree_handles_broken_symlink(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "real.txt").write_text("content", encoding="utf-8")
    os.symlink("/nonexistent/target", root / "broken")
    entries = fsapi.list_tree(root, "")
    names = [e["name"] for e in entries]
    assert "broken" in names
    broken_entry = next(e for e in entries if e["name"] == "broken")
    assert broken_entry["type"] == "file"
    assert broken_entry["size"] == 0


def test_resolve_rejects_null_byte(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(fsapi.FsError):
        fsapi.resolve_in_project(root, "foo\x00bar")


def test_read_file_maps_oserror_to_fserror(tmp_path):
    import os
    if os.geteuid() == 0:
        pytest.skip("chmod-based perm test is no-op as root")
    root = tmp_path / "proj"
    root.mkdir()
    secret = root / "secret.txt"
    secret.write_text("sensitive", encoding="utf-8")
    secret.chmod(0o000)
    try:
        with pytest.raises(fsapi.FsError):
            fsapi.read_file(root, "secret.txt")
    finally:
        secret.chmod(0o644)
