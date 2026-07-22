"""Regression tests for remote app preview (the 2026-07 appview breakage).

Real dev servers Host-check requests (Vite allowedHosts), reference assets by
root-absolute path (`/assets/app.js`, `/@vite/client`), and run HMR over a
WebSocket to the page origin. The sub-path proxy (`/api/appview/<slug>/`) can
serve none of that to remote clients: absolute paths escape the prefix onto the
Proxima UI origin, the opaque iframe sandbox drops the session cookie on every
subresource, and there is no WS upgrade. The per-app preview relay must handle
all three — these tests drive it against a fixture dev server that mimics them.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
from http.cookies import SimpleCookie

import httpx
import pytest
import uvicorn
import websockets

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.preview_proxy import PreviewRelayManager


class FakeDevServer:
    """Vite-like fixture: allowed-host checking, root-absolute asset paths,
    a WS echo endpoint, and a record of the headers each request arrived with."""

    def __init__(self) -> None:
        self.port: int | None = None
        self.seen: dict[str, dict[str, str]] = {}

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            offered = scope.get("subprotocols") or []
            accept = {"type": "websocket.accept"}
            if offered:
                accept["subprotocol"] = offered[0]
            await send(accept)
            while True:
                m = await receive()
                if m["type"] == "websocket.disconnect":
                    return
                if m["type"] == "websocket.receive" and m.get("text") is not None:
                    await send({"type": "websocket.send", "text": m["text"]})
            return
        if scope["type"] != "http":
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        self.seen[scope["path"]] = headers
        host = headers.get("host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            body, status, ctype = f"Blocked request. This host ({host!r}) is not allowed.".encode(), 403, b"text/plain"
        elif scope["path"] == "/":
            body, status, ctype = b'<!doctype html><script type="module" src="/assets/app.js"></script>', 200, b"text/html"
        elif scope["path"] == "/assets/app.js":
            body, status, ctype = b"console.log('real app code')", 200, b"text/javascript"
        else:
            body, status, ctype = b"not found", 404, b"text/plain"
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", ctype)]})
        await send({"type": "http.response.body", "body": body})


class _TestServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self) -> None:
        pass


async def _start_upstream(asgi) -> tuple[uvicorn.Server, asyncio.Task, socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = int(sock.getsockname()[1])
    server = _TestServer(uvicorn.Config(asgi, lifespan="off", access_log=False, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    return server, task, sock, port


@contextlib.asynccontextmanager
async def _relay_against_fake_devserver(validate_token=lambda t: t == "good-token"):
    fake = FakeDevServer()
    server, task, sock, upstream_port = await _start_upstream(fake)
    fake.port = upstream_port
    relays = PreviewRelayManager("127.0.0.1", port_for=lambda slug: upstream_port if slug == "demo" else None,
                                 validate_token=validate_token)
    try:
        relay_port = await relays.start("demo")
        yield fake, relay_port
    finally:
        await relays.shutdown()
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)
        with contextlib.suppress(OSError):
            sock.close()


def test_relay_serves_root_absolute_assets_with_host_rewrite_and_credential_stripping():
    async def run_case():
        async with _relay_against_fake_devserver() as (fake, relay_port):
            base = f"http://127.0.0.1:{relay_port}"
            # The browser would send the Proxima session cookie + the preview
            # capability; project code must see neither.
            headers = {"Cookie": "proxima_session=owner-secret; proxima_preview=good-token",
                       "Authorization": "Bearer owner-secret"}
            async with httpx.AsyncClient() as client:
                page = await client.get(base + "/", headers=headers)
                # 200 proves the Host rewrite: the fixture rejects any Host but its own
                # (the browser sent Host 127.0.0.1:<relay port>, a la Vite allowedHosts).
                assert page.status_code == 200
                assert "/assets/app.js" in page.text
                # The regression core: the root-absolute asset path resolves on the
                # relay origin — through the sub-path proxy it escaped to the UI origin.
                asset = await client.get(base + "/assets/app.js", headers=headers)
                assert asset.status_code == 200
                assert asset.text == "console.log('real app code')"
            for path in ("/", "/assets/app.js"):
                assert "cookie" not in fake.seen[path]
                assert "authorization" not in fake.seen[path]

    asyncio.run(run_case())


def test_relay_requires_preview_capability_and_running_app():
    async def run_case():
        async with _relay_against_fake_devserver() as (fake, relay_port):
            base = f"http://127.0.0.1:{relay_port}"
            async with httpx.AsyncClient() as client:
                assert (await client.get(base + "/")).status_code == 403
                bad = await client.get(base + "/", headers={"Cookie": "proxima_preview=forged"})
                assert bad.status_code == 403
            assert fake.seen == {}  # nothing unauthorized ever reached project code
        # Same relay shape, but the app is gone: capability holds, target doesn't.
        relays = PreviewRelayManager("127.0.0.1", port_for=lambda slug: None,
                                     validate_token=lambda t: t == "good-token")
        try:
            port = await relays.start("demo")
            async with httpx.AsyncClient() as client:
                gone = await client.get(f"http://127.0.0.1:{port}/",
                                        headers={"Cookie": "proxima_preview=good-token"})
                assert gone.status_code == 503
        finally:
            await relays.shutdown()

    asyncio.run(run_case())


def test_relay_proxies_websocket_hmr_upgrade():
    async def run_case():
        async with _relay_against_fake_devserver() as (_fake, relay_port):
            async with websockets.connect(
                f"ws://127.0.0.1:{relay_port}/hmr",
                subprotocols=["vite-hmr"],
                additional_headers={"Cookie": "proxima_preview=good-token"},
                open_timeout=10,
            ) as ws:
                assert ws.subprotocol == "vite-hmr"
                await ws.send('{"type":"ping"}')
                assert await asyncio.wait_for(ws.recv(), timeout=10) == '{"type":"ping"}'

    asyncio.run(run_case())


def _app(tmp_path, **overrides):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        **overrides,
    })


def test_preview_auth_sets_host_scoped_cookie_without_apps_domain(tmp_path):
    client = TestClient(_app(tmp_path))
    token = client.post("/auth/auto").json()["token"]
    response = client.post("/api/preview-auth", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    cookie = SimpleCookie()
    cookie.load(set_cookie)
    assert cookie["proxima_preview"].value
    # Host-only (no Domain=) so the browser also sends it to <host>:<relay port>,
    # and not Secure over plain http (Tailscale/LAN deployments).
    assert not cookie["proxima_preview"]["domain"]
    assert not cookie["proxima_preview"]["secure"]


def _a_non_loopback_local_ip() -> str | None:
    """Some non-loopback address of this host (LAN/tailnet), if it has one.
    UDP connect sends no packet; the kernel just picks the routed source IP."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 1))  # TEST-NET-1, never actually contacted
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def test_app_start_reports_preview_port_and_relay_serves_the_app(tmp_path):
    with TestClient(_app(tmp_path, preview_bind_host="127.0.0.1")) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post("/api/projects", json={"slug": "demo", "name": "Demo"}, headers=auth).status_code == 201
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            app_port = int(probe.getsockname()[1])
        assert client.post("/api/projects/demo/app/start", headers=auth,
                           json={"command": "python3 -m http.server $PORT --bind 127.0.0.1",
                                 "port": app_port, "dir": ""}).json()["ok"]
        try:
            status = {}
            for _ in range(80):
                status = client.get("/api/projects/demo/app/status", headers=auth).json()
                if status.get("ready"):
                    break
                import time
                time.sleep(0.05)
            assert status.get("ready") is True
            assert isinstance(status.get("preview_port"), int)
            assert status["preview_port"] != status["port"]
            # The audit's F1 reproduction must now fail: the loopback-bound dev
            # server is NOT reachable on a non-loopback address (no unauth read
            # of the project tree from another LAN/tailnet device) …
            assert not status.get("broad_bind")
            off_host_ip = _a_non_loopback_local_ip()
            if off_host_ip:
                with pytest.raises(OSError):
                    socket.create_connection((off_host_ip, status["port"]), timeout=1).close()

            # … but the same app IS previewable through the capability-gated relay.
            preview_cookie = client.post("/api/preview-auth", headers=auth).cookies["proxima_preview"]
            page = httpx.get(f"http://127.0.0.1:{status['preview_port']}/",
                             cookies={"proxima_preview": preview_cookie}, timeout=10)
            assert page.status_code == 200
            assert "Directory listing" in page.text
        finally:
            assert client.post("/api/projects/demo/app/stop", headers=auth).json()["ok"]
        # Relay is reaped with the app: its port must stop accepting connections.
        with pytest.raises(httpx.TransportError):
            httpx.get(f"http://127.0.0.1:{status['preview_port']}/", timeout=2)


def test_detect_apps_suggested_commands_bind_loopback(tmp_path):
    """Audit F1: the product's own suggestions must not open the project tree to
    the LAN - every suggested server command binds 127.0.0.1 explicitly."""
    with TestClient(_app(tmp_path)) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post("/api/projects", json={"slug": "demo", "name": "Demo"}, headers=auth).status_code == 201
        client.put("/api/projects/demo/file", params={"path": "site/index.html"},
                   json={"content": "<h1>hi</h1>"}, headers=auth)
        client.put("/api/projects/demo/file", params={"path": "django/manage.py"},
                   json={"content": "#"}, headers=auth)
        apps = {a["kind"]: a["command"] for a in client.get("/api/projects/demo/apps", headers=auth).json()["apps"]}
        assert apps["static · index.html"] == "python3 -m http.server $PORT --bind 127.0.0.1"
        assert apps["django"] == "python manage.py runserver 127.0.0.1:$PORT"


def _wait_ready(client, auth) -> dict:
    import time
    status: dict = {}
    for _ in range(80):
        status = client.get("/api/projects/demo/app/status", headers=auth).json()
        if status.get("ready"):
            return status
        time.sleep(0.05)
    return status


def test_broadly_bound_dev_server_surfaces_warning(tmp_path):
    """A command Proxima cannot rewrite may still bind all interfaces; app status
    must flag it (broad_bind) so the UI can warn the owner."""
    with TestClient(_app(tmp_path, preview_bind_host="127.0.0.1")) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post("/api/projects", json={"slug": "demo", "name": "Demo"}, headers=auth).status_code == 201
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            app_port = int(probe.getsockname()[1])
        # No --bind: python's http.server listens on all interfaces (the F1 shape).
        assert client.post("/api/projects/demo/app/start", headers=auth,
                           json={"command": "python3 -m http.server $PORT",
                                 "port": app_port, "dir": ""}).json()["ok"]
        try:
            status = _wait_ready(client, auth)
            assert status.get("ready") is True
            assert status.get("broad_bind") is True
        finally:
            assert client.post("/api/projects/demo/app/stop", headers=auth).json()["ok"]


def test_app_subprocess_defaults_host_to_loopback(tmp_path):
    """Frameworks that honor $HOST must inherit a loopback default from Proxima."""
    with TestClient(_app(tmp_path, preview_bind_host="127.0.0.1")) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post("/api/projects", json={"slug": "demo", "name": "Demo"}, headers=auth).status_code == 201
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            app_port = int(probe.getsockname()[1])
        assert client.post("/api/projects/demo/app/start", headers=auth,
                           json={"command": 'echo "host=$HOST" && python3 -m http.server $PORT --bind 127.0.0.1',
                                 "port": app_port, "dir": ""}).json()["ok"]
        try:
            status = _wait_ready(client, auth)
            assert status.get("ready") is True
            assert "host=127.0.0.1" in (status.get("log") or [])
        finally:
            assert client.post("/api/projects/demo/app/stop", headers=auth).json()["ok"]
