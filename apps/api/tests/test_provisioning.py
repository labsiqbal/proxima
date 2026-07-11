from __future__ import annotations

import pytest

from proxima_api import db as dbmod
from proxima_api import provisioning
from proxima_api.auth import hash_password, iso_now


def make_db():
    conn = dbmod.connect(":memory:")
    dbmod.init_db(conn)
    return conn


def make_cfg(tmp_path):
    return {
        "workspace_root": str(tmp_path),
        "provision_starter_dirs": ["wiki", "tasks", "artifacts"],
        "default_team_name": "Team",
        "auto_provision": True,
    }


def add_user(conn, username, role="member"):
    cur = conn.execute(
        "INSERT INTO users(username, os_user, role, password_hash, password_set_at) VALUES (?, ?, ?, ?, ?)",
        (username, username, role, hash_password("password1"), iso_now()),
    )
    return dict(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())



def test_fresh_schema_has_no_project_members_table():
    conn = make_db()
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_members'").fetchone() is None

def test_team_name_round_trip_and_fallback(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    assert provisioning.get_team_name(conn, cfg) == "Team"  # fallback to cfg
    provisioning.set_team_name(conn, "Linc")
    assert provisioning.get_team_name(conn, cfg) == "Linc"


def test_scaffold_project_dir_creates_folders_and_readme(tmp_path):
    cfg = make_cfg(tmp_path)
    path = provisioning.scaffold_project_dir(cfg, "demo")
    assert path == tmp_path / "projects" / "demo"
    assert (path / "wiki").is_dir()
    assert (path / "tasks").is_dir()
    assert (path / "artifacts").is_dir()
    assert (path / "README.md").read_text().startswith("# demo")


def test_scaffold_is_idempotent(tmp_path):
    cfg = make_cfg(tmp_path)
    provisioning.scaffold_project_dir(cfg, "demo")
    provisioning.scaffold_project_dir(cfg, "demo")  # must not raise
    assert (tmp_path / "projects" / "demo" / "wiki").is_dir()


def test_provision_private_project(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    user = add_user(conn, "alice")
    project = provisioning.provision_private_project(conn, cfg, user)
    assert project["slug"] == "alice"
    assert project["visibility"] == "private"
    assert project["owner_user_id"] == user["id"]
    assert (tmp_path / "projects" / "alice" / "wiki").is_dir()
    actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()]
    assert "workspace.provision.private" in actions


def test_provision_private_idempotent(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    user = add_user(conn, "alice")
    provisioning.provision_private_project(conn, cfg, user)
    provisioning.provision_private_project(conn, cfg, user)
    count = conn.execute("SELECT COUNT(*) AS c FROM projects WHERE owner_user_id = ?", (user["id"],)).fetchone()["c"]
    assert count == 1


def test_provision_private_slug_collision_uses_home_suffix(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    admin = add_user(conn, "admin", role="environment_admin")
    conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id, visibility) VALUES ('team', 'Team', '/x', ?, 'shared')",
        (admin["id"],),
    )
    user = add_user(conn, "team")
    project = provisioning.provision_private_project(conn, cfg, user)
    assert project["slug"] == "team-home"


def test_provision_shared_records_owner_without_membership_rows(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    admin = add_user(conn, "admin", role="environment_admin")
    add_user(conn, "bob")
    project = provisioning.provision_shared_project(conn, cfg, "linc", "Linc", admin)
    assert project["visibility"] == "shared"
    assert project["owner_user_id"] == admin["id"]
    assert (tmp_path / "projects" / "linc" / "wiki").is_dir()
    actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()]
    assert "workspace.provision.shared" in actions


def test_provision_shared_idempotent(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    admin = add_user(conn, "admin", role="environment_admin")
    provisioning.provision_shared_project(conn, cfg, "linc", "Linc", admin)
    provisioning.provision_shared_project(conn, cfg, "linc", "Linc", admin)
    count = conn.execute("SELECT COUNT(*) AS c FROM projects WHERE slug = 'linc'").fetchone()["c"]
    assert count == 1


def test_user_workspace_provisions_private_only_with_existing_shared(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    admin = add_user(conn, "admin", role="environment_admin")
    provisioning.provision_shared_project(conn, cfg, "linc", "Linc", admin)
    bob = add_user(conn, "bob")
    provisioning.provision_user_workspace(conn, cfg, bob)
    assert conn.execute("SELECT COUNT(*) AS c FROM projects WHERE slug = 'bob'").fetchone()["c"] == 1
    shared = conn.execute("SELECT owner_user_id FROM projects WHERE slug = 'linc'").fetchone()
    assert shared["owner_user_id"] == admin["id"]


def test_user_workspace_error_is_isolated(tmp_path, monkeypatch):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    bob = add_user(conn, "bob")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(provisioning, "scaffold_project_dir", boom)
    provisioning.provision_user_workspace(conn, cfg, bob)  # must NOT raise
    actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()]
    assert "workspace.provision.error" in actions


def test_auto_provision_disabled_is_noop(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    cfg["auto_provision"] = False
    bob = add_user(conn, "bob")
    provisioning.provision_user_workspace(conn, cfg, bob)
    assert conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"] == 0


def test_backfill_all_users(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    admin = add_user(conn, "admin", role="environment_admin")
    add_user(conn, "bob")
    add_user(conn, "carol")
    provisioning.provision_shared_project(conn, cfg, "linc", "Linc", admin)
    summary = provisioning.backfill(conn, cfg)
    assert summary["users"] == 3
    assert conn.execute("SELECT COUNT(*) AS c FROM projects WHERE visibility = 'private'").fetchone()["c"] == 3
    assert conn.execute("SELECT COUNT(*) AS c FROM projects WHERE slug = 'linc' AND visibility = 'shared'").fetchone()["c"] == 1


def test_backfill_idempotent(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    add_user(conn, "bob")
    provisioning.backfill(conn, cfg)
    provisioning.backfill(conn, cfg)
    assert conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"] == 1


def test_default_config_has_provisioning_keys():
    from proxima_api.settings import normalize_config

    cfg = normalize_config()
    assert cfg["default_team_name"] == "Team"
    assert cfg["provision_starter_dirs"] == ["wiki", "tasks", "artifacts"]
    assert cfg["auto_provision"] is True


def _client(tmp_path):
    from fastapi.testclient import TestClient
    from proxima_api.main import create_app

    app = create_app(
        {
            "database_path": str(tmp_path / "db.sqlite"),
            "workspace_root": str(tmp_path / "ws"),
            "hermes_profiles_root": str(tmp_path / "profiles"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    return TestClient(app)


def test_provision_private_when_owns_shared_same_slug_uses_home(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    admin = add_user(conn, "linc", role="environment_admin")
    provisioning.provision_shared_project(conn, cfg, "linc", "Linc", admin)
    project = provisioning.provision_private_project(conn, cfg, admin)
    assert project["slug"] == "linc-home"
    assert project["visibility"] == "private"


def test_provision_shared_rejects_non_shared_slug(tmp_path):
    conn = make_db()
    cfg = make_cfg(tmp_path)
    alice = add_user(conn, "alice")
    provisioning.provision_private_project(conn, cfg, alice)  # owns private slug 'alice'
    import pytest
    with pytest.raises(ValueError):
        provisioning.provision_shared_project(conn, cfg, "alice", "Alice", alice)


# ---------------------------------------------------------------------------
# FIX 1 — cross-user leak on double slug collision
# ---------------------------------------------------------------------------

def test_private_project_never_joins_another_users_project(tmp_path):
    """User B named 'team' must NOT become owner of user A's 'team-home' project."""
    conn = make_db()
    cfg = make_cfg(tmp_path)

    # User A is named "team-home" and already has their own private project.
    user_a = add_user(conn, "team-home")
    proj_a = provisioning.provision_private_project(conn, cfg, user_a)
    assert proj_a["slug"] == "team-home"

    # A shared project occupies the slug "team" so user B can't use it directly.
    admin = add_user(conn, "admin", role="environment_admin")
    conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id, visibility) VALUES ('team', 'Team', '/x', ?, 'shared')",
        (admin["id"],),
    )

    # User B is named "team".
    user_b = add_user(conn, "team")
    proj_b = provisioning.provision_private_project(conn, cfg, user_b)

    # B must NOT have slug 'team-home' (that belongs to A).
    assert proj_b["slug"] != "team-home", "B stole A's slug"
    # A's project ownership must remain unchanged; access is owner_user_id-scoped.
    a_project = conn.execute("SELECT owner_user_id FROM projects WHERE id = ?", (proj_a["id"],)).fetchone()
    assert a_project["owner_user_id"] == user_a["id"]
    # B's project must be owned by B and be private.
    assert proj_b["owner_user_id"] == user_b["id"]
    assert proj_b["visibility"] == "private"


# ---------------------------------------------------------------------------
# FIX 2 — bootstrap shared-project failure is non-fatal
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FIX 4 — validate project name length / emptiness
# ---------------------------------------------------------------------------

def test_project_create_name_too_long_is_422(tmp_path):
    """Project name longer than 120 chars must be rejected with 422."""
    client = _client(tmp_path)
    token = client.post("/auth/auto").json()["token"]
    resp = client.post(
        "/api/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "ops", "name": "N" * 121},
    )
    assert resp.status_code == 422, resp.text


def test_project_create_empty_name_is_422(tmp_path):
    """Project name that is empty (or whitespace-only) must be rejected with 422."""
    client = _client(tmp_path)
    token = client.post("/auth/auto").json()["token"]
    resp = client.post(
        "/api/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "ops", "name": "   "},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# FIX 5 — defense-in-depth in scaffold_project_dir
# ---------------------------------------------------------------------------

def test_scaffold_rejects_unsafe_slug(tmp_path):
    """scaffold_project_dir must raise ValueError for slugs containing path-traversal chars."""
    import pytest
    cfg = make_cfg(tmp_path)
    with pytest.raises(ValueError, match="unsafe slug"):
        provisioning.scaffold_project_dir(cfg, "../evil")
    with pytest.raises(ValueError, match="unsafe slug"):
        provisioning.scaffold_project_dir(cfg, "foo/bar")
    with pytest.raises(ValueError, match="unsafe slug"):
        provisioning.scaffold_project_dir(cfg, ".hidden")
    with pytest.raises(ValueError, match="unsafe slug"):
        provisioning.scaffold_project_dir(cfg, "foo\\bar")


def test_refresh_credentials_overwrites_stale_copy(tmp_path):
    # seed copies once and never updates; refresh updates when the host rotates.
    from proxima_api.profile_seed import seed_hermes_home, refresh_hermes_credentials
    src = tmp_path / "src"; src.mkdir()
    (src / "auth.json").write_text("token-v1"); (src / "config.yaml").write_text("cfg")
    tgt = tmp_path / "tgt"; tgt.mkdir()
    seed_hermes_home(src, tgt)
    assert (tgt / "auth.json").read_text() == "token-v1"
    # host rotates its OAuth token
    (src / "auth.json").write_text("token-v2")
    seed_hermes_home(src, tgt)  # idempotent: does NOT update
    assert (tgt / "auth.json").read_text() == "token-v1"
    changed = refresh_hermes_credentials(src, tgt)  # refresh DOES update
    assert "auth.json" in changed
    assert (tgt / "auth.json").read_text() == "token-v2"
    assert refresh_hermes_credentials(src, tgt) == []  # idempotent when unchanged
