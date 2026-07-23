from __future__ import annotations

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
    assert list(target.iterdir()) == []  # empty; never scaffolded or copied into


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
    assert "already exists" in r.json()["detail"].lower()
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
    assert "outside" in r.json()["detail"].lower()
    assert not outside_target.exists()

    r = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(root / ".."), "mkdir": True},
    )
    assert r.status_code == 400
    assert "invalid folder name" in r.json()["detail"].lower()

    r = c.post(
        "/api/projects/link",
        headers=h,
        json={"path": str(root / "missing-parent" / "child"), "mkdir": True},
    )
    assert r.status_code == 400
    assert "parent" in r.json()["detail"].lower()
    assert not (root / "missing-parent").exists()


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
