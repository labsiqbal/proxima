from __future__ import annotations

import errno
import os
from pathlib import Path

from fastapi.testclient import TestClient

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
    r = c.post("/api/projects/link", headers=h, json={"path": str(folder), "slug": "Bad_Slug"})
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "slug"


def _link_client(tmp_path: Path, roots: list[Path] | None = None) -> tuple[TestClient, dict[str, str]]:
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


def test_link_mkdir_creates_folder_and_registers_project(tmp_path: Path):
    parent = tmp_path / "code"
    parent.mkdir()
    c, h = _link_client(tmp_path)
    target = parent / "fresh-app"
    assert not target.exists()
    r = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(target), "name": "Fresh App", "mkdir": True},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "fresh-app"
    assert body["name"] == "Fresh App"
    assert body["path"] == str(target.resolve())
    assert target.is_dir()
    assert [path.name for path in target.iterdir()] == ["ops"]
    assert (target / "ops" / "container.md").is_file()


def test_link_mkdir_rejects_existing_name(tmp_path: Path):
    parent = tmp_path / "code"
    parent.mkdir()
    existing = parent / "taken"
    existing.mkdir()
    (existing / "keep-me.txt").write_text("stay", encoding="utf-8")
    c, h = _link_client(tmp_path)
    r = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(existing), "mkdir": True},
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
    r = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(outside_target), "mkdir": True},
    )
    assert r.status_code == 403
    assert "outside" in r.json()["detail"]["message"].lower()
    assert r.json()["detail"]["field"] == "parent"
    assert not outside_target.exists()

    r = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(root / ".."), "mkdir": True},
    )
    assert r.status_code == 400
    assert "invalid folder name" in r.json()["detail"]["message"].lower()
    assert r.json()["detail"]["field"] == "folder"

    r = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(root / "missing-parent" / "child"), "mkdir": True},
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
    original_mkdir = Path.mkdir

    def deny_target(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError("denied")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_target)
    response = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(target), "mkdir": True},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "permission denied - cannot create folder here",
        "field": "parent",
    }
    assert not target.exists()


def test_link_mkdir_routes_unreadable_parent_probe_to_parent(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "blocked"
    c, h = _link_client(tmp_path)
    original_is_dir = Path.is_dir

    def deny_parent(path: Path):
        if path == parent:
            raise PermissionError("denied")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", deny_parent)
    response = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(target), "mkdir": True},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "permission denied - parent directory is not accessible",
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

    near = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(parent / near_name), "name": "Near limit", "mkdir": True},
    )
    assert near.status_code == 201, near.text
    assert (parent / near_name).is_dir()

    over = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(parent / over_name), "name": "Over limit", "mkdir": True},
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
    response = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "message": "folder name cannot be encoded for this filesystem",
        "field": "folder",
    }
    assert not target.exists()


def test_link_mkdir_keeps_full_path_name_too_long_on_parent(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "code"
    parent.mkdir()
    target = parent / "short-name"
    c, h = _link_client(tmp_path)
    original_mkdir = Path.mkdir

    def reject_full_path(path: Path, *args, **kwargs):
        if path == target:
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", reject_full_path)
    response = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(target), "mkdir": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "parent"
    assert not target.exists()


def test_browse_recovers_to_nearest_readable_ancestor(tmp_path: Path):
    root = tmp_path / "allowed"
    ancestor = root / "code"
    ancestor.mkdir(parents=True)
    (ancestor / "visible").mkdir()
    c, h = _link_client(tmp_path, roots=[root])

    response = c.get(
        "/api/fs/dirs",
        headers=h,
        params={"path": str(ancestor / "missing" / "child")},
    )

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
    original_iterdir = Path.iterdir

    def deny_selected(path: Path):
        if path == selected:
            raise PermissionError("denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_selected)
    response = c.get(
        "/api/fs/dirs",
        headers=h,
        params={"path": str(selected)},
    )

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
    original_is_dir = Path.is_dir

    def deny_selected_status(path: Path):
        if path == selected:
            raise PermissionError("denied")
        return original_is_dir(path)

    def deny_root_traversal(path: Path):
        if path == root:
            raise PermissionError("denied")
        return iter(())

    monkeypatch.setattr(Path, "is_dir", deny_selected_status)
    monkeypatch.setattr(Path, "iterdir", deny_root_traversal)
    response = c.get(
        "/api/fs/dirs",
        headers=h,
        params={"path": str(selected)},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "No readable folder is available inside the allowed roots",
        "field": "path",
    }


def test_browse_never_traverses_outside_allowed_roots(tmp_path: Path):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "visible").mkdir()
    (outside / "private").mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    c, h = _link_client(tmp_path, roots=[root])

    response = c.get(
        "/api/fs/dirs",
        headers=h,
        params={"path": str(outside / "private")},
    )

    assert response.status_code == 200
    assert response.json()["path"] == str(root)
    assert response.json()["dirs"] == [
        {"name": "visible", "path": str(root / "visible")},
    ]


def test_browse_skips_self_and_mutual_symlink_cycles(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "visible").mkdir()
    (root / "self-cycle").symlink_to("self-cycle")
    (root / "mutual-a").symlink_to("mutual-b")
    (root / "mutual-b").symlink_to("mutual-a")
    c, h = _link_client(tmp_path, roots=[root])

    response = c.get("/api/fs/dirs", headers=h, params={"path": str(root)})

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

    response = c.get("/api/fs/dirs", headers=h, params={"path": str(loop)})

    assert response.status_code == 200
    assert response.json()["path"] == str(nested)
    assert response.json()["dirs"] == []


def test_link_and_create_symlink_cycles_return_structured_fields(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    loop = root / "loop"
    loop.symlink_to("loop")
    c, h = _link_client(tmp_path, roots=[root])

    link = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(loop)},
    )
    assert link.status_code == 400
    assert link.json()["detail"]["field"] == "path"

    create = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(loop / "child"), "mkdir": True},
    )
    assert create.status_code == 400
    assert create.json()["detail"]["field"] == "parent"


def test_unresolvable_configured_root_returns_structured_path_error(tmp_path: Path):
    root = tmp_path / "root-cycle"
    root.symlink_to("root-cycle")
    c, h = _link_client(tmp_path, roots=[root])

    response = c.get("/api/fs/dirs", headers=h)

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "No readable folder is available inside the allowed roots",
        "field": "path",
    }


def test_link_mkdir_routes_derived_slug_collisions_to_name(tmp_path: Path):
    parent = tmp_path / "code"
    parent.mkdir()
    existing = parent / "existing"
    existing.mkdir()
    c, h = _link_client(tmp_path)
    first = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(existing), "name": "Shared Name"},
    )
    assert first.status_code == 201, first.text

    target = parent / "different-folder"
    collision = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(target), "name": "Shared Name", "mkdir": True},
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
        c.post(
            "/api/projects/link",
            headers=h,
            json={"path": str(target), "name": "Orphan Me", "mkdir": True},
        )
        raise AssertionError("expected unexpected failure to propagate")
    except RuntimeError as exc:
        assert "simulated unexpected failure" in str(exc)
    assert not target.exists()
