from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.updates import read_local_version


def test_health_endpoint_reports_ready(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["product"] == "proxima"
    assert body["service"] == "proxima"
    assert body["version"] == read_local_version()
    assert body["database"] == "ok"
    assert body["worker"] == "disabled"
