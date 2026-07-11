from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from fastapi.testclient import TestClient

from proxima_api.main import create_app

ROOT = Path("/srv/proxima-demo-api")
REPO = Path(__file__).resolve().parents[3]
PROJECTCTL = REPO / "infra" / "scripts" / "projectctl"
DB = ROOT / "proxima.db"
PROJECT = "deltapack-api"


def sh(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout.strip()


def sudo_cmd(cmd: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return cmd
    return ["sudo", *cmd]


def as_user_cmd(user: str, cmd: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return ["su", "-s", "/bin/bash", user, "-c", " ".join(cmd)]
    return ["sudo", "-u", user, *cmd]


def can_read(user: str, path: Path) -> bool:
    return subprocess.run(as_user_cmd(user, ["test", "-r", str(path)]), check=False).returncode == 0


def login(client: TestClient, username: str) -> dict[str, str]:
    res = client.post("/auth/login", json={"username": username})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def main() -> None:
    if not PROJECTCTL.exists():
        raise SystemExit(f"projectctl not found: {PROJECTCTL}")

    if ROOT.exists():
        sh(sudo_cmd(["rm", "-rf", str(ROOT)]))

    print(sh(sudo_cmd([str(PROJECTCTL), "init-workspace", str(ROOT), "--owner", "alice"])))

    app = create_app(
        {
            "database_path": str(DB),
            "workspace_root": str(ROOT),
            "projectctl_command": sudo_cmd([str(PROJECTCTL)]),
            "seed_users": [
                {"username": "alice", "os_user": "alice", "role": "environment_admin"},
                {"username": "bob", "os_user": "bob", "role": "member"},
            ],
        }
    )
    client = TestClient(app)
    bob = login(client, "bob")
    alice = login(client, "alice")

    res = client.post("/api/projects", json={"slug": PROJECT, "name": "Deltapack API"}, headers=bob)
    assert res.status_code == 201, res.text
    project_path = ROOT / "projects" / PROJECT
    print(f"created={res.json()['slug']} path={project_path}")

    assert can_read("bob", project_path)
    assert not can_read("alice", project_path)
    assert client.get("/api/projects", headers=alice).json()["projects"] == []
    print("before_invite: aris_can_read=YES kuya_can_read=NO")

    res = client.post(f"/api/projects/{PROJECT}/invite", json={"username": "alice"}, headers=bob)
    assert res.status_code == 200, res.text
    assert can_read("alice", project_path)
    assert [p["slug"] for p in client.get("/api/projects", headers=alice).json()["projects"]] == [PROJECT]
    print("after_invite: kuya_can_read=YES kuya_api_visible=YES")

    res = client.post(f"/api/projects/{PROJECT}/remove", json={"username": "alice"}, headers=bob)
    assert res.status_code == 200, res.text
    assert not can_read("alice", project_path)
    assert client.get("/api/projects", headers=alice).json()["projects"] == []
    print("after_remove: kuya_can_read=NO kuya_api_visible=NO")

    acl = sh(["getfacl", "-p", str(ROOT), str(ROOT / "projects"), str(project_path)])
    print("\n--- ACL ---")
    print("\n".join(acl.splitlines()[:80]))

    print("\nSMOKE_OK")


if __name__ == "__main__":
    main()
