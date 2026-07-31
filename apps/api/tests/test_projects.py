from __future__ import annotations

import errno
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import container_registry, project_browse
from proxima_api.directory_handles import directory_identity_for_path
from proxima_api.main import create_app


def make_projectctl(tmp_path: Path) -> Path:
    log_path = tmp_path / "projectctl.log"
    script = tmp_path / "fake-projectctl"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        f"log = pathlib.Path({str(log_path)!r})\n"
        "log.write_text(log.read_text() + ' '.join(sys.argv[1:]) + '\\n' if log.exists() else ' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def client(tmp_path: Path) -> TestClient:
    ctl = make_projectctl(tmp_path)
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": str(ctl),
        }
    )
    return TestClient(app)


def auth_headers(api: TestClient) -> dict[str, str]:
    # Single-user cockpit: no login wall — /auth/auto returns the sole owner + token.
    res = api.post("/auth/auto")
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _failing_ctl(tmp_path: Path) -> Path:
    """A projectctl that always fails — stands in for projectctl needing root."""
    script = tmp_path / "failing-ctl"
    script.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit('must run as root/sudo')\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_project_create_works_without_privileged_helper(tmp_path: Path):
    # Default manage_os_acl=False (single-user $HOME install): creating a project
    # must NOT invoke the privileged projectctl helper. Point projectctl at a
    # script that always fails to prove it is never called.
    app = create_app({
        "database_path": str(tmp_path / "h.db"),
        "workspace_root": str(tmp_path / "rt"),
        "projectctl_path": str(_failing_ctl(tmp_path)),
    })
    api = TestClient(app)
    res = api.post("/api/projects", json={"slug": "freshproj", "name": "Fresh"}, headers=auth_headers(api))
    assert res.status_code == 201, res.text
    assert (tmp_path / "rt" / "projects" / "freshproj").is_dir()  # dir scaffolded on disk


def test_project_create_invokes_helper_when_manage_os_acl(tmp_path: Path):
    # The /srv multi-user deployment opts in: the helper IS invoked, so a failing
    # one surfaces as a 500 (proving the privileged path runs when enabled).
    app = create_app({
        "database_path": str(tmp_path / "h.db"),
        "workspace_root": str(tmp_path / "rt"),
        "projectctl_path": str(_failing_ctl(tmp_path)),
        "manage_os_acl": True,
    })
    api = TestClient(app)
    res = api.post("/api/projects", json={"slug": "freshproj", "name": "Fresh"}, headers=auth_headers(api))
    assert res.status_code == 500
    assert "root" in res.text.lower()


def test_link_project_invalid_slug_returns_422_not_500(tmp_path):
    # A bad explicit slug (or an auto-derived one ending in '-') must yield a clean
    # 4xx, not a 500 from validate_slug's raw ValueError.
    folder = tmp_path / "myfolder"
    folder.mkdir()
    app = create_app({
        "database_path": str(tmp_path / "h.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(tmp_path)],
        "start_worker": False,
    })
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = _post_link(c, h, {"path": str(folder), "slug": "Bad_Slug"})
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "slug"


def _link_client(
    tmp_path: Path,
    roots: list[str | Path] | None = None,
) -> tuple[TestClient, dict[str, str]]:
    app = create_app({
        "database_path": str(tmp_path / "h.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(p) for p in (roots or [tmp_path])],
        "start_worker": False,
    })
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _browse_dirs(
    c: TestClient,
    headers: dict[str, str],
    path: str = "",
    root_id: str | None = None,
):
    if path and root_id is None:
        initial = c.get("/api/fs/dirs", headers=headers)
        assert initial.status_code == 200, initial.text
        root_id = initial.json()["root_id"]
    params = {"path": path}
    if root_id is not None:
        params["root_id"] = root_id
    return c.get("/api/fs/dirs", headers=headers, params=params)


def _post_link(
    c: TestClient,
    headers: dict[str, str],
    payload: dict[str, object],
):
    body = dict(payload)
    if "root_id" not in body:
        initial = c.get("/api/fs/dirs", headers=headers)
        assert initial.status_code == 200, initial.text
        body["root_id"] = initial.json()["root_id"]
    return c.post("/api/projects/link", headers=headers, json=body)


def test_link_mkdir_creates_folder_and_registers_project(tmp_path: Path):
    parent = tmp_path / "code"
    parent.mkdir()
    c, h = _link_client(tmp_path)
    target = parent / "fresh-app"
    assert not target.exists()
    r = _post_link(
        c,
        h,
        {"path": str(target), "name": "Fresh App", "mkdir": True},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "fresh-app"
    assert body["name"] == "Fresh App"
    assert body["path"] == str(target.resolve())
    assert target.is_dir()
    assert [path.name for path in target.iterdir()] == ["ops"]
    assert (target / "ops" / "container.md").is_file()


def test_folder_requests_require_returned_root_identity(tmp_path: Path):
    root = tmp_path / "allowed"
    child = root / "child"
    child.mkdir(parents=True)
    c, h = _link_client(tmp_path, roots=[root])

    browse = c.get(
        "/api/fs/dirs",
        headers=h,
        params={"path": str(child)},
    )
    link = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(child)},
    )

    assert browse.status_code == 403
    assert browse.json()["detail"]["field"] == "path"
    assert link.status_code == 422


def test_link_mkdir_rejects_existing_name(tmp_path: Path):
    parent = tmp_path / "code"
    parent.mkdir()
    existing = parent / "taken"
    existing.mkdir()
    (existing / "keep-me.txt").write_text("stay", encoding="utf-8")
    c, h = _link_client(tmp_path)
    r = _post_link(
        c,
        h,
        {"path": str(existing), "mkdir": True},
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]["message"].lower()
    assert r.json()["detail"]["field"] == "folder"
    # Must not delete or alter the existing folder.
    assert (existing / "keep-me.txt").read_text(encoding="utf-8") == "stay"


def test_link_mkdir_rejects_outside_roots_and_bad_names(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    c, h = _link_client(tmp_path, roots=[root])

    outside_target = outside / "nope"
    r = _post_link(
        c,
        h,
        {"path": str(outside_target), "mkdir": True},
    )
    assert r.status_code == 403
    assert "outside" in r.json()["detail"]["message"].lower()
    assert r.json()["detail"]["field"] == "parent"
    assert not outside_target.exists()

    r = _post_link(
        c,
        h,
        {"path": str(root / ".."), "mkdir": True},
    )
    assert r.status_code == 400
    assert "invalid folder name" in r.json()["detail"]["message"].lower()
    assert r.json()["detail"]["field"] == "folder"

    r = _post_link(
        c,
        h,
        {"path": str(root / "missing-parent" / "child"), "mkdir": True},
    )
    assert r.status_code == 400
    assert "parent" in r.json()["detail"]["message"].lower()
    assert r.json()["detail"]["field"] == "parent"
    assert not (root / "missing-parent").exists()


def test_link_mkdir_routes_parent_permission_failure_to_parent(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "blocked"
    c, h = _link_client(tmp_path)
    original_mkdir = os.mkdir

    def deny_target(path, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and str(path).startswith(".proxima-create-"):
            raise PermissionError("denied")
        if dir_fd is None:
            return original_mkdir(path, mode)
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", deny_target)
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "permission denied - cannot create folder here",
        "field": "parent",
    }
    assert not target.exists()


def test_link_mkdir_routes_unreadable_parent_descriptor_to_parent(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "blocked"
    c, h = _link_client(tmp_path)
    original_open = os.open

    def deny_parent(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and path == parent.name:
            raise PermissionError("denied")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", deny_parent)
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "permission denied - cannot create folder here",
        "field": "parent",
    }
    assert not target.exists()


def test_link_mkdir_validates_multibyte_component_bytes(tmp_path: Path):
    parent = tmp_path / "code"
    parent.mkdir()
    c, h = _link_client(tmp_path)
    limit = os.pathconf(parent, "PC_NAME_MAX")
    unit = "é"
    unit_bytes = len(os.fsencode(unit))
    near_name = unit * (limit // unit_bytes)
    over_name = unit * ((limit // unit_bytes) + 1)

    near = _post_link(
        c,
        h,
        {"path": str(parent / near_name), "name": "Near limit", "mkdir": True},
    )
    assert near.status_code == 201, near.text
    assert (parent / near_name).is_dir()

    over = _post_link(
        c,
        h,
        {"path": str(parent / over_name), "name": "Over limit", "mkdir": True},
    )
    assert over.status_code == 400
    assert over.json()["detail"]["field"] == "folder"
    assert f"maximum {limit} bytes" in over.json()["detail"]["message"]
    assert over_name not in {entry.name for entry in parent.iterdir()}


def test_link_mkdir_routes_component_encoding_failure_to_folder(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "encoding-error"
    c, h = _link_client(tmp_path)
    original_fsencode = os.fsencode

    def reject_component(value):
        if value == target.name:
            raise UnicodeEncodeError("utf-8", value, 0, 1, "unsupported")
        return original_fsencode(value)

    monkeypatch.setattr(os, "fsencode", reject_component)
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "message": "folder name cannot be encoded for this filesystem",
        "field": "folder",
    }
    assert not target.exists()


def test_link_mkdir_routes_post_syscall_component_too_long_to_folder(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "short-name"
    c, h = _link_client(tmp_path)

    def reject_component(*_args, **_kwargs):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(project_browse._backend, "publish", reject_component)
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "folder"
    assert not target.exists()


def test_link_mkdir_routes_native_invalid_component_to_folder(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "short-name"
    c, h = _link_client(tmp_path)

    def reject_component(*_args, **_kwargs):
        raise project_browse.DirectoryNameError(errno.EINVAL, "invalid name")

    monkeypatch.setattr(project_browse._backend, "publish", reject_component)
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "folder"
    assert not target.exists()


def test_link_mkdir_routes_post_create_publish_failure_to_parent(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "short-name"
    c, h = _link_client(tmp_path)

    def reject_created_component(*_args, **_kwargs):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(
        project_browse._backend,
        "publish",
        reject_created_component,
    )
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "parent"
    assert not target.exists()


def test_link_mkdir_keeps_long_parent_failure_on_parent(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "short-name"
    c, h = _link_client(tmp_path)
    original_open = os.open

    def reject_parent(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and path == parent.name:
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", reject_parent)
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "parent"
    assert not target.exists()


def test_link_mkdir_rejects_intermediate_symlink_swap(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "allowed"
    parent = root / "middle" / "parent"
    outside = tmp_path / "outside"
    outside_parent = outside / "parent"
    parent.mkdir(parents=True)
    outside_parent.mkdir(parents=True)
    c, h = _link_client(tmp_path, roots=[root])
    target = parent / "escaped"
    original_create = project_browse.create_directory_component

    def swap_then_create(resolved_parent, name, mode=0o755):
        middle = root / "middle"
        middle.rename(root / "middle-original")
        middle.symlink_to(outside, target_is_directory=True)
        return original_create(resolved_parent, name, mode)

    monkeypatch.setattr(
        "proxima_api.routes.projects.create_directory_component",
        swap_then_create,
    )
    response = _post_link(
        c,
        h,
        {"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "parent"
    assert not (outside_parent / target.name).exists()


def test_link_mkdir_rolls_back_through_retained_parent_descriptor(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "allowed"
    parent = root / "middle" / "parent"
    outside = tmp_path / "outside"
    outside_parent = outside / "parent"
    parent.mkdir(parents=True)
    outside_parent.mkdir(parents=True)
    outside_target = outside_parent / "orphan-me"
    outside_target.mkdir()
    c, h = _link_client(tmp_path, roots=[root])
    target = parent / outside_target.name

    def swap_then_fail(_slug: str) -> str:
        middle = root / "middle"
        middle.rename(root / "middle-original")
        middle.symlink_to(outside, target_is_directory=True)
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(
        "proxima_api.routes.projects.validate_slug",
        swap_then_fail,
    )
    try:
        _post_link(
            c,
            h,
            {"path": str(target), "name": "Orphan Me", "mkdir": True},
        )
        raise AssertionError("expected unexpected failure to propagate")
    except RuntimeError as exc:
        assert "simulated unexpected failure" in str(exc)

    assert outside_target.is_dir()
    assert not (root / "middle-original" / "parent" / target.name).exists()


def test_browse_recovers_to_nearest_readable_ancestor(tmp_path: Path):
    root = tmp_path / "allowed"
    ancestor = root / "code"
    ancestor.mkdir(parents=True)
    (ancestor / "visible").mkdir()
    c, h = _link_client(tmp_path, roots=[root])

    response = _browse_dirs(c, h, str(ancestor / "missing" / "child"))

    assert response.status_code == 200
    assert response.json()["path"] == str(ancestor)
    assert response.json()["dirs"] == [
        {"name": "visible", "path": str(ancestor / "visible")},
    ]


def test_browse_recovers_from_unreadable_selection(tmp_path: Path, monkeypatch):
    root = tmp_path / "allowed"
    selected = root / "blocked"
    selected.mkdir(parents=True)
    c, h = _link_client(tmp_path, roots=[root])
    original_open = project_browse._open_directory_under_root

    def deny_selected(root_path: Path, path: Path, root_identity: str):
        if path == selected:
            raise PermissionError("denied")
        return original_open(root_path, path, root_identity)

    monkeypatch.setattr(project_browse, "_open_directory_under_root", deny_selected)
    response = _browse_dirs(c, h, str(selected))

    assert response.status_code == 200
    assert response.json()["path"] == str(root)


def test_browse_retains_error_when_no_allowed_ancestor_is_readable(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "allowed"
    selected = root / "blocked"
    selected.mkdir(parents=True)
    c, h = _link_client(tmp_path, roots=[root])
    initial = _browse_dirs(c, h)
    assert initial.status_code == 200
    root_id = initial.json()["root_id"]
    original_open = project_browse._open_directory_under_root

    def deny_candidates(root_path: Path, path: Path, root_identity: str):
        if path in (selected, root):
            raise PermissionError("denied")
        return original_open(root_path, path, root_identity)

    monkeypatch.setattr(project_browse, "_open_directory_under_root", deny_candidates)
    response = _browse_dirs(c, h, str(selected), root_id)

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "No readable folder is available inside the allowed roots",
        "field": "path",
    }


def test_browse_rejects_intermediate_symlink_swap(tmp_path: Path, monkeypatch):
    root = tmp_path / "allowed"
    selected = root / "middle" / "selected"
    outside = tmp_path / "outside"
    selected.mkdir(parents=True)
    outside.mkdir()
    (outside / "private").mkdir()
    c, h = _link_client(tmp_path, roots=[root])
    original_open = project_browse._open_directory_under_root
    swapped = False

    def swap_then_open(root_path: Path, path: Path, root_identity: str):
        nonlocal swapped
        if path == selected and not swapped:
            swapped = True
            (root / "middle").rename(root / "middle-original")
            (root / "middle").symlink_to(outside, target_is_directory=True)
        return original_open(root_path, path, root_identity)

    monkeypatch.setattr(project_browse, "_open_directory_under_root", swap_then_open)
    response = _browse_dirs(c, h, str(selected))

    assert response.status_code == 200
    assert response.json()["path"] == str(root)
    assert all(entry["name"] != "private" for entry in response.json()["dirs"])


def test_browse_never_traverses_outside_allowed_roots(tmp_path: Path):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "visible").mkdir()
    (outside / "private").mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    c, h = _link_client(tmp_path, roots=[root])

    response = _browse_dirs(c, h, str(outside / "private"))

    assert response.status_code == 403
    assert response.json()["detail"]["field"] == "path"


def test_browse_skips_self_and_mutual_symlink_cycles(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "visible").mkdir()
    (root / "self-cycle").symlink_to("self-cycle")
    (root / "mutual-a").symlink_to("mutual-b")
    (root / "mutual-b").symlink_to("mutual-a")
    c, h = _link_client(tmp_path, roots=[root])

    response = _browse_dirs(c, h, str(root))

    assert response.status_code == 200
    assert response.json()["path"] == str(root)
    assert response.json()["dirs"] == [
        {"name": "visible", "path": str(root / "visible")},
    ]


def test_browse_recovers_from_requested_symlink_cycle_to_parent(tmp_path: Path):
    root = tmp_path / "allowed"
    nested = root / "nested"
    nested.mkdir(parents=True)
    loop = nested / "loop"
    loop.symlink_to("loop")
    c, h = _link_client(tmp_path, roots=[root])

    response = _browse_dirs(c, h, str(loop))

    assert response.status_code == 200
    assert response.json()["path"] == str(nested)
    assert response.json()["dirs"] == []


def test_link_and_create_symlink_cycles_return_structured_fields(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    loop = root / "loop"
    loop.symlink_to("loop")
    c, h = _link_client(tmp_path, roots=[root])

    link = _post_link(
        c,
        h,
        {"path": str(loop)},
    )
    assert link.status_code == 400
    assert link.json()["detail"]["field"] == "path"

    create = _post_link(
        c,
        h,
        {"path": str(loop / "child"), "mkdir": True},
    )
    assert create.status_code == 400
    assert create.json()["detail"]["field"] == "parent"


def test_unresolvable_configured_root_returns_structured_path_error(tmp_path: Path):
    root = tmp_path / "root-cycle"
    root.symlink_to("root-cycle")
    c, h = _link_client(tmp_path, roots=[root])

    response = _browse_dirs(c, h)

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "No readable folder is available inside the allowed roots",
        "field": "path",
    }


def test_unexpandable_root_keeps_valid_siblings_available(tmp_path: Path):
    missing_home = "~definitely-no-such-proxima-test-user"
    valid = tmp_path / "valid"
    valid.mkdir()
    c, h = _link_client(tmp_path, roots=[missing_home, valid])

    response = _browse_dirs(c, h)

    assert response.status_code == 200
    assert response.json()["path"] == str(valid)
    assert response.json()["roots"] == [missing_home, str(valid)]

    missing_root_id = project_browse.AllowedRoots.from_configured(
        [missing_home, valid]
    ).roots[0].id
    retained = _browse_dirs(
        c,
        h,
        f"{missing_home}/retained-selection",
        missing_root_id,
    )
    assert retained.status_code == 403
    assert retained.json()["detail"] == {
        "message": "Selected folder root is not reachable",
        "field": "path",
    }


def test_failed_configured_root_selection_never_falls_back_to_another_root(
    tmp_path: Path,
):
    valid = tmp_path / "valid"
    valid.mkdir()
    failed = tmp_path / "failed"
    failed.symlink_to("failed")
    c, h = _link_client(tmp_path, roots=[valid, failed])

    failed_root_id = project_browse.AllowedRoots.from_configured(
        [valid, failed]
    ).roots[1].id
    response = _browse_dirs(
        c,
        h,
        str(failed / "retained-selection"),
        failed_root_id,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "Selected folder root is not reachable",
        "field": "path",
    }

    valid_response = _browse_dirs(c, h, str(valid))
    assert valid_response.status_code == 200
    assert valid_response.json()["roots"] == [str(valid), str(failed)]


def test_nested_root_mutation_never_falls_back_to_containing_root(
    tmp_path: Path,
    monkeypatch,
):
    outer = tmp_path / "outer"
    nested = outer / "nested"
    nested.mkdir(parents=True)
    nested_root_id = project_browse.AllowedRoots.from_configured(
        [outer, nested]
    ).roots[1].id
    original_resolve = project_browse._resolve
    nested_resolutions = 0

    def mutate_nested_root(value):
        nonlocal nested_resolutions
        if Path(value) == nested:
            nested_resolutions += 1
            if nested_resolutions == 2:
                nested.rename(outer / "nested-original")
                nested.symlink_to("nested")
        return original_resolve(value)

    monkeypatch.setattr(project_browse, "_resolve", mutate_nested_root)
    c, h = _link_client(tmp_path, roots=[outer, nested])
    response = _browse_dirs(
        c,
        h,
        str(nested / "retained-selection"),
        nested_root_id,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "Selected folder root is not reachable",
        "field": "path",
    }


def test_link_mkdir_routes_derived_slug_collisions_to_name(tmp_path: Path):
    parent = tmp_path / "code"
    parent.mkdir()
    existing = parent / "existing"
    existing.mkdir()
    c, h = _link_client(tmp_path)
    first = _post_link(
        c,
        h,
        {"path": str(existing), "name": "Shared Name"},
    )
    assert first.status_code == 201, first.text

    target = parent / "different-folder"
    collision = _post_link(
        c,
        h,
        {"path": str(target), "name": "Shared Name", "mkdir": True},
    )
    assert collision.status_code == 409
    assert collision.json()["detail"] == {
        "message": "A project with that display name already exists - choose another display name",
        "field": "name",
    }
    assert not target.exists()


def test_link_mkdir_removes_dir_on_unexpected_error(tmp_path: Path, monkeypatch):
    """Unexpected post-mkdir failure (before the project row lands) must not leave an orphan dir."""
    parent = tmp_path / "code"
    parent.mkdir()
    c, h = _link_client(tmp_path)
    target = parent / "orphan-me"

    def boom(_slug: str) -> str:
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr("proxima_api.routes.projects.validate_slug", boom)
    try:
        _post_link(
            c,
            h,
            {"path": str(target), "name": "Orphan Me", "mkdir": True},
        )
        raise AssertionError("expected unexpected failure to propagate")
    except RuntimeError as exc:
        assert "simulated unexpected failure" in str(exc)
    assert not target.exists()


def test_symlink_root_id_survives_canonical_browse_and_link(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    child = real / "child"
    child.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    c, h = _link_client(tmp_path, roots=[alias, tmp_path])

    initial = _browse_dirs(c, h)
    assert initial.status_code == 200
    body = initial.json()
    assert body["path"] == str(real)
    assert body["parent"] is None
    root_id = body["root_id"]

    retained = _browse_dirs(
        c,
        h,
        str(real),
        root_id,
    )
    assert retained.status_code == 200
    assert retained.json()["root_id"] == root_id
    assert retained.json()["parent"] is None

    linked = _post_link(
        c,
        h,
        {
            "path": str(child),
            "root_id": root_id,
            "name": "Alias child",
        },
    )
    assert linked.status_code == 201, linked.text
    with sqlite3.connect(tmp_path / "h.db") as conn:
        identity = conn.execute(
            "SELECT path_identity FROM projects WHERE slug = 'alias-child'"
        ).fetchone()[0]
    assert identity.startswith("posix:")


def test_canonical_symlink_target_never_falls_back_to_containing_root(
    tmp_path: Path,
):
    real = tmp_path / "real"
    real.mkdir()
    child = real / "child"
    child.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    roots = project_browse.AllowedRoots.from_configured([alias, tmp_path])

    resolved = roots.resolve(child, roots.roots[0].id)
    browsed = project_browse.browse_directory(
        str(child),
        [alias, tmp_path],
        roots.roots[0].id,
    )

    assert resolved.root == real
    assert resolved.root_id == roots.roots[0].id
    assert browsed["root_id"] == roots.roots[0].id


def test_root_id_is_stable_when_configured_root_order_changes(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    original = project_browse.AllowedRoots.from_configured([first, second])
    reordered = project_browse.AllowedRoots.from_configured([second, first])

    assert original.roots[0].id == reordered.roots[1].id
    assert original.roots[1].id == reordered.roots[0].id


def test_root_id_rejects_same_path_replacement(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    c, h = _link_client(tmp_path, roots=[root])
    initial = _browse_dirs(c, h)
    root_id = initial.json()["root_id"]

    root.rename(tmp_path / "allowed-original")
    root.mkdir()
    response = _browse_dirs(
        c,
        h,
        str(root),
        root_id,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["field"] == "path"


def test_resolved_root_identity_is_pinned_across_descriptor_reopen(
    tmp_path: Path,
):
    root = tmp_path / "allowed"
    parent = root / "parent"
    parent.mkdir(parents=True)
    roots = project_browse.AllowedRoots.from_configured([root])
    root_id = roots.roots[0].id
    resolved_parent = roots.resolve(parent, root_id)

    root.rename(tmp_path / "allowed-original")
    replacement_parent = root / "parent"
    replacement_parent.mkdir(parents=True)

    with pytest.raises(
        project_browse.ConfiguredRootUnavailable,
        match="root changed",
    ):
        project_browse.create_directory_component(
            resolved_parent,
            "must-not-exist",
        )
    assert not (replacement_parent / "must-not-exist").exists()


def test_linked_project_rejects_replacement_platform_identity(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    linked = root / "linked"
    linked.mkdir()
    c, h = _link_client(tmp_path, roots=[root])
    response = _post_link(
        c,
        h,
        {"path": str(linked), "name": "Linked"},
    )
    assert response.status_code == 201, response.text
    with sqlite3.connect(tmp_path / "h.db") as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute(
            "SELECT * FROM projects WHERE slug = 'linked'"
        ).fetchone()

    linked.rename(root / "linked-original")
    linked.mkdir()

    with pytest.raises(
        container_registry.ContainerBoundaryError,
        match="identity changed",
    ):
        container_registry.container_root(project)


def test_atomic_publish_never_replaces_existing_entry(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "raced"
    c, h = _link_client(tmp_path)
    original_publish = project_browse._backend.publish

    def install_replacement_then_publish(
        parent_handle,
        child_handle,
        staging_name,
        final_name,
    ):
        target.mkdir()
        (target / "replacement.txt").write_text("keep", encoding="utf-8")
        return original_publish(
            parent_handle,
            child_handle,
            staging_name,
            final_name,
        )

    monkeypatch.setattr(
        project_browse._backend,
        "publish",
        install_replacement_then_publish,
    )
    response = _post_link(
        c,
        h,
        {"path": str(target), "name": "Raced", "mkdir": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["field"] == "folder"
    assert (target / "replacement.txt").read_text(encoding="utf-8") == "keep"
    assert not any(path.name.startswith(".proxima-create-") for path in parent.iterdir())
    with sqlite3.connect(tmp_path / "h.db") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM projects WHERE slug = 'raced'"
        ).fetchone()[0] == 0


def test_post_publish_ancestor_swap_returns_parent_error_and_rolls_back_owned_dir(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "allowed"
    parent = root / "middle" / "parent"
    outside = tmp_path / "outside"
    outside_parent = outside / "parent"
    parent.mkdir(parents=True)
    outside_parent.mkdir(parents=True)
    target = parent / "created"
    replacement = outside_parent / target.name
    replacement.mkdir()
    (replacement / "keep.txt").write_text("keep", encoding="utf-8")
    c, h = _link_client(tmp_path, roots=[root])
    original_publish = project_browse._backend.publish

    def publish_then_swap(
        parent_handle,
        child_handle,
        staging_name,
        final_name,
    ):
        original_publish(
            parent_handle,
            child_handle,
            staging_name,
            final_name,
        )
        middle = root / "middle"
        middle.rename(root / "middle-original")
        middle.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        project_browse._backend,
        "publish",
        publish_then_swap,
    )
    response = _post_link(
        c,
        h,
        {"path": str(target), "name": "Created", "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "parent"
    assert (replacement / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (root / "middle-original" / "parent" / target.name).exists()
    with sqlite3.connect(tmp_path / "h.db") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM projects WHERE slug = 'created'"
        ).fetchone()[0] == 0


def test_rollback_removes_owned_staging_without_deleting_replacement(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "rollback-race"
    c, h = _link_client(tmp_path)
    replacement: Path | None = None
    moved = parent / "moved-owned-staging"

    def replace_staging_then_fail(_slug: str) -> str:
        nonlocal replacement
        staging = next(
            path for path in parent.iterdir()
            if path.name.startswith(".proxima-create-")
        )
        staging.rename(moved)
        staging.mkdir()
        replacement = staging
        raise RuntimeError("simulated publish race")

    monkeypatch.setattr(
        "proxima_api.routes.projects.validate_slug",
        replace_staging_then_fail,
    )
    with pytest.raises(RuntimeError, match="simulated publish race"):
        _post_link(
            c,
            h,
            {"path": str(target), "name": "Rollback Race", "mkdir": True},
        )

    assert replacement is not None and replacement.is_dir()
    assert not moved.exists()
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle regression")
def test_windows_junctions_are_not_browsed_or_used_for_creation(tmp_path: Path):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    junction = root / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(OSError):
        directory_identity_for_path(junction)
    c, h = _link_client(tmp_path, roots=[root])

    initial = _browse_dirs(c, h)
    assert initial.status_code == 200
    assert all(entry["name"] != "junction" for entry in initial.json()["dirs"])
    created = _post_link(
        c,
        h,
        {
            "path": str(root / "native-created"),
            "root_id": initial.json()["root_id"],
            "name": "Native created",
            "mkdir": True,
        },
    )
    assert created.status_code == 201, created.text
    one_character = _post_link(
        c,
        h,
        {
            "path": str(root / "x"),
            "root_id": initial.json()["root_id"],
            "name": "X",
            "mkdir": True,
        },
    )
    assert one_character.status_code == 201, one_character.text
    with sqlite3.connect(tmp_path / "h.db") as conn:
        identity = conn.execute(
            "SELECT path_identity FROM projects WHERE slug = 'native-created'"
        ).fetchone()[0]
    assert identity.startswith("windows:")
