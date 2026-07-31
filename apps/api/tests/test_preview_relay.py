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
import os
import signal
import socket
import subprocess
from http.cookies import SimpleCookie
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
import websockets

from fastapi.testclient import TestClient

from apps.safe_updater.write_fence import (
    prepare_ingress_lock,
    write as write_fence,
)
from proxima_api import apprunner
from proxima_api.main import create_app
from proxima_api.maintenance_status import MaintenanceBoundary
from proxima_api.preview_output import OutputBrokerUnavailable
from proxima_api.preview_proxy import PreviewProxyMiddleware, PreviewRelayManager


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


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _start_upstream(asgi) -> tuple[uvicorn.Server, asyncio.Task, socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = int(sock.getsockname()[1])
    server = _TestServer(uvicorn.Config(
        asgi,
        lifespan="off",
        access_log=False,
        log_level="warning",
        ws="websockets-sansio",
    ))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    return server, task, sock, port


@contextlib.asynccontextmanager
async def _relay_against_fake_devserver(
    validate_token=lambda t: t == "good-token",
    maintenance=None,
):
    fake = FakeDevServer()
    server, task, sock, upstream_port = await _start_upstream(fake)
    fake.port = upstream_port
    relays = PreviewRelayManager(
        "127.0.0.1",
        port_for=lambda slug: upstream_port if slug == "demo" else None,
        verify_connection=lambda slug, port, client_port: (
            slug == "demo" and port == upstream_port and client_port > 0
        ),
        validate_token=validate_token,
        maintenance=maintenance,
    )
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
                                     verify_connection=lambda slug, port, client_port: False,
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


def test_unauthenticated_preview_does_not_resolve_managed_target():
    async def run_case():
        relay_resolutions = 0

        def relay_target(_slug):
            nonlocal relay_resolutions
            relay_resolutions += 1
            return 5180

        relays = PreviewRelayManager(
            "127.0.0.1",
            port_for=relay_target,
            verify_connection=lambda *_args: False,
            validate_token=lambda _token: False,
        )
        relay = relays._asgi_for("demo")
        relay_messages = []

        async def receive():
            return {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }

        async def relay_send(message):
            relay_messages.append(message)

        await relay(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [],
            },
            receive,
            relay_send,
        )

        class Manager:
            resolutions = 0

            def preview_target(self, _slug):
                self.resolutions += 1
                return 5180

            def verify_preview_connection(self, *_args):
                raise AssertionError("verification must follow authentication")

        manager = Manager()

        async def downstream(_scope, _receive, _send):
            raise AssertionError("preview subdomain must not reach downstream")

        middleware = PreviewProxyMiddleware(
            downstream,
            SimpleNamespace(
                state=SimpleNamespace(app_manager=manager),
            ),
            "apps.example.test",
            validate_token=lambda _token: False,
        )
        middleware_messages = []

        async def middleware_send(message):
            middleware_messages.append(message)

        await middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [
                    (b"host", b"preview-demo.apps.example.test"),
                ],
            },
            receive,
            middleware_send,
        )

        assert relay_messages[0]["status"] == 403
        assert middleware_messages[0]["status"] == 403
        assert relay_resolutions == 0
        assert manager.resolutions == 0

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


def test_relay_denies_fenced_requests_before_upstream(tmp_path):
    async def run_case():
        fence = tmp_path / "status" / "fence.json"
        maintenance = MaintenanceBoundary(
            {"safe_update_fence_path": str(fence)}
        )
        prepare_ingress_lock(fence)
        async with _relay_against_fake_devserver(
            maintenance=maintenance
        ) as (fake, relay_port):
            base = f"http://127.0.0.1:{relay_port}"
            headers = {"Cookie": "proxima_preview=good-token"}
            async with httpx.AsyncClient() as client:
                assert (await client.get(base + "/", headers=headers)).status_code == 200
                fake.seen.clear()
                write_fence(fence, "d" * 32, "write_fenced")
                denied = await client.get(base + "/", headers=headers)
            assert denied.status_code == 423
            assert fake.seen == {}

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


def test_app_start_refuses_an_existing_preview_port_without_stopping_it(tmp_path):
    """The browser-facing start API must not briefly proxy a foreign preview."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        app_port = int(probe.getsockname()[1])
    foreign = subprocess.Popen(
        ["python3", "-m", "http.server", str(app_port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(80):
            try:
                socket.create_connection(("127.0.0.1", app_port), timeout=0.05).close()
                break
            except OSError:
                import time
                time.sleep(0.025)
        else:
            pytest.fail("foreign preview did not start")
        with TestClient(_app(tmp_path)) as client:
            token = client.post("/auth/auto").json()["token"]
            auth = {"Authorization": f"Bearer {token}"}
            assert client.post("/api/projects", json={"slug": "demo", "name": "Demo"}, headers=auth).status_code == 201
            response = client.post("/api/projects/demo/app/start", headers=auth, json={
                "command": "python3 -m http.server $PORT --bind 127.0.0.1",
                "port": app_port,
                "dir": "",
            })
            assert response.status_code == 409
            detail = response.json()["detail"]
            assert detail["state"] == "port_conflict"
            assert detail["port"] == app_port
            assert "already in use" in detail["message"]
            assert foreign.poll() is None
            status = client.get("/api/projects/demo/app/status", headers=auth).json()
            assert status["state"] == "port_conflict"
            assert status["running"] is False
    finally:
        foreign.terminate()
        foreign.wait(timeout=5)


def test_app_start_reports_recoverable_output_broker_failure(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/projects",
            json={"slug": "demo", "name": "Demo"},
            headers=auth,
        ).status_code == 201

        async def unavailable():
            raise OutputBrokerUnavailable(
                "Preview output broker could not start"
            )

        client.app.state.app_manager._output_broker_factory = unavailable
        response = client.post(
            "/api/projects/demo/app/start",
            headers=auth,
            json={
                "command": "sleep 60",
                "port": _free_port(),
                "dir": "",
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"] == {
            "state": "stopped",
            "reason": "output_sink_unavailable",
            "message": "Preview output broker could not start",
        }
        status = client.get(
            "/api/projects/demo/app/status",
            headers=auth,
        ).json()
        assert status["state"] == "stopped"
        assert status["reason"] == "output_sink_unavailable"
        assert not client.app.state.app_manager._apps


def test_post_preflight_port_theft_never_reaches_the_foreign_listener(
    tmp_path,
    monkeypatch,
):
    """Deterministically take the candidate after preflight. Both preview front
    doors must fail closed, status must become terminal, and Stop may signal only
    the managed process group."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        app_port = int(probe.getsockname()[1])
    foreign_log = tmp_path / "foreign-http.log"
    foreign_output = foreign_log.open("wb")
    foreign: subprocess.Popen | None = None
    original_port_open = apprunner._port_open

    def steal_after_preflight(port: int) -> bool:
        nonlocal foreign
        if port == app_port and foreign is None:
            foreign = subprocess.Popen(
                [
                    "python3",
                    "-m",
                    "http.server",
                    str(app_port),
                    "--bind",
                    "127.0.0.1",
                ],
                cwd=tmp_path,
                stdout=subprocess.DEVNULL,
                stderr=foreign_output,
                start_new_session=True,
            )
            for _ in range(80):
                if original_port_open(app_port):
                    break
                import time
                time.sleep(0.025)
            else:
                pytest.fail("foreign listener did not claim the candidate port")
            return False
        return original_port_open(port)

    monkeypatch.setattr(apprunner, "_port_open", steal_after_preflight)
    try:
        with TestClient(_app(tmp_path, preview_bind_host="127.0.0.1")) as client:
            token = client.post("/auth/auto").json()["token"]
            auth = {"Authorization": f"Bearer {token}"}
            assert client.post(
                "/api/projects",
                json={"slug": "demo", "name": "Demo"},
                headers=auth,
            ).status_code == 201

            started = client.post(
                "/api/projects/demo/app/start",
                headers=auth,
                json={"command": "sleep 60", "port": app_port, "dir": ""},
            )
            assert started.status_code == 200

            status = {}
            for _ in range(80):
                status = client.get(
                    "/api/projects/demo/app/status",
                    headers=auth,
                ).json()
                if status.get("state") == "port_conflict":
                    break
                import time
                time.sleep(0.025)

            assert status["state"] == "port_conflict"
            assert status["running"] is False
            assert status["ready"] is False
            assert status["requested_port"] == app_port
            assert "port" not in status
            assert foreign is not None and foreign.poll() is None

            appview = client.get("/api/appview/demo/", headers=auth)
            assert appview.status_code == 503
            assert appview.json()["detail"]["state"] == "port_conflict"

            preview_cookie = client.post(
                "/api/preview-auth",
                headers=auth,
            ).cookies["proxima_preview"]
            relay = httpx.get(
                f"http://127.0.0.1:{status['preview_port']}/",
                cookies={"proxima_preview": preview_cookie},
                timeout=5,
            )
            assert relay.status_code == 503

            assert client.post(
                "/api/projects/demo/app/stop",
                headers=auth,
            ).json()["ok"]
            assert foreign.poll() is None
            foreign_output.flush()
            assert foreign_log.read_text() == ""
    finally:
        foreign_output.close()
        if foreign is not None:
            foreign.terminate()
            foreign.wait(timeout=5)


def test_stop_rebind_between_target_lookup_and_connect_fails_closed(
    tmp_path,
    monkeypatch,
):
    with TestClient(_app(tmp_path, preview_bind_host="127.0.0.1")) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/projects",
            json={"slug": "demo", "name": "Demo"},
            headers=auth,
        ).status_code == 201
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            app_port = int(probe.getsockname()[1])
        assert client.post(
            "/api/projects/demo/app/start",
            headers=auth,
            json={
                "command": (
                    "python3 -m http.server $PORT --bind 127.0.0.1"
                ),
                "port": app_port,
                "dir": "",
            },
        ).status_code == 200
        status = _wait_ready(client, auth)
        assert status["state"] == "ready"

        manager = client.app.state.app_manager
        original_target = manager.preview_target
        foreign_log = tmp_path / "rebound-foreign.log"
        foreign_output = foreign_log.open("wb")
        foreign: subprocess.Popen | None = None
        armed = True

        def stop_and_rebind(slug: str) -> int | None:
            nonlocal armed, foreign
            target = original_target(slug)
            if target is None or not armed:
                return target
            armed = False
            managed = manager._apps[slug]["proc"]
            os.killpg(os.getpgid(managed.pid), signal.SIGTERM)
            for _ in range(100):
                if not apprunner._port_open(target):
                    break
                import time
                time.sleep(0.01)
            foreign = subprocess.Popen(
                [
                    "python3",
                    "-m",
                    "http.server",
                    str(target),
                    "--bind",
                    "127.0.0.1",
                ],
                cwd=tmp_path,
                stdout=subprocess.DEVNULL,
                stderr=foreign_output,
                start_new_session=True,
            )
            for _ in range(100):
                if apprunner._port_open(target):
                    break
                import time
                time.sleep(0.01)
            return target

        monkeypatch.setattr(manager, "preview_target", stop_and_rebind)
        preview_cookie = client.post(
            "/api/preview-auth",
            headers=auth,
        ).cookies["proxima_preview"]
        try:
            relay = httpx.get(
                f"http://127.0.0.1:{status['preview_port']}/",
                cookies={"proxima_preview": preview_cookie},
                timeout=5,
            )
            assert relay.status_code == 503
            current = client.get(
                "/api/projects/demo/app/status",
                headers=auth,
            ).json()
            assert current["state"] == "port_conflict"
            appview = client.get("/api/appview/demo/", headers=auth)
            assert appview.status_code == 503
            assert appview.json()["detail"]["state"] == "port_conflict"
            assert foreign is not None and foreign.poll() is None
            assert client.post(
                "/api/projects/demo/app/stop",
                headers=auth,
            ).status_code == 200
            assert foreign.poll() is None
            foreign_output.flush()
            assert foreign_log.read_text() == ""
        finally:
            foreign_output.close()
            if foreign is not None:
                foreign.terminate()
                foreign.wait(timeout=5)


def test_appview_returns_non_proxy_responses_for_starting_unknown_and_exited(
    tmp_path,
    monkeypatch,
):
    with TestClient(_app(tmp_path, preview_bind_host="127.0.0.1")) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/projects",
            json={"slug": "demo", "name": "Demo"},
            headers=auth,
        ).status_code == 201
        preview_cookie = client.post(
            "/api/preview-auth",
            headers=auth,
        ).cookies["proxima_preview"]

        def assert_relay_unavailable(port: int) -> None:
            response = httpx.get(
                f"http://127.0.0.1:{port}/",
                cookies={"proxima_preview": preview_cookie},
                timeout=5,
            )
            assert response.status_code == 503

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            candidate = int(probe.getsockname()[1])
        assert client.post(
            "/api/projects/demo/app/start",
            headers=auth,
            json={"command": "sleep 60", "port": candidate, "dir": ""},
        ).status_code == 200
        starting_status = client.get(
            "/api/projects/demo/app/status",
            headers=auth,
        ).json()
        assert starting_status["state"] == "starting"
        assert_relay_unavailable(starting_status["preview_port"])
        starting = client.get("/api/appview/demo/", headers=auth)
        assert starting.status_code == 503
        assert starting.json()["detail"]["state"] == "starting"

        monkeypatch.setattr(
            apprunner,
            "_port_open",
            lambda port: port == candidate,
        )
        monkeypatch.setattr(
            apprunner,
            "_listener_ownership",
            lambda _port, *, authority: (
                apprunner.PortOwnership.UNKNOWN
            ),
        )
        unknown = client.get("/api/appview/demo/", headers=auth)
        assert unknown.status_code == 503
        assert unknown.json()["detail"]["state"] == "ownership_unknown"
        unknown_status = client.get(
            "/api/projects/demo/app/status",
            headers=auth,
        ).json()
        assert unknown_status["state"] == "ownership_unknown"
        assert_relay_unavailable(unknown_status["preview_port"])
        assert client.post(
            "/api/projects/demo/app/stop",
            headers=auth,
        ).status_code == 200

        monkeypatch.undo()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            exit_candidate = int(probe.getsockname()[1])
        assert client.post(
            "/api/projects/demo/app/start",
            headers=auth,
            json={"command": "exit 0", "port": exit_candidate, "dir": ""},
        ).status_code == 200
        status = {}
        for _ in range(80):
            status = client.get(
                "/api/projects/demo/app/status",
                headers=auth,
            ).json()
            if status.get("state") == "exited":
                break
            import time
            time.sleep(0.025)
        assert status["state"] == "exited"
        exited = client.get("/api/appview/demo/", headers=auth)
        assert exited.status_code == 503
        assert exited.json()["detail"]["state"] == "exited"
        assert_relay_unavailable(status["preview_port"])
        assert client.post(
            "/api/projects/demo/app/stop",
            headers=auth,
        ).status_code == 200
        stopped_status = client.get(
            "/api/projects/demo/app/status",
            headers=auth,
        ).json()
        assert stopped_status["state"] == "stopped"
        assert stopped_status["command"] == status["command"]
        assert stopped_status["log"] == status["log"]
        stopped = client.get("/api/appview/demo/", headers=auth)
        assert stopped.status_code == 503
        assert stopped.json()["detail"]["state"] == "stopped"


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
        assert apps["django"] == "python3 manage.py runserver 127.0.0.1:$PORT"


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
