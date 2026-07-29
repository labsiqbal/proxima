"""Installed trusted API, SSE, asset, version and browser probe suite."""
from __future__ import annotations

import ctypes
import hashlib
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(url: str, token: str | None = None) -> tuple[dict, bytes]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urlopen(Request(url, headers=headers), timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status for {url}")
        raw = response.read()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected JSON payload for {url}")
    return value, raw


def _bytes(url: str) -> tuple[bytes, str]:
    with urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status for {url}")
        return response.read(), str(response.headers.get("content-type") or "")


def _asset_digest(root: Path) -> str:
    files: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("candidate static assets contain a symlink")
        if path.is_file():
            files.append((path.relative_to(root).as_posix(), _sha(path.read_bytes())))
    if not files:
        raise RuntimeError("candidate static assets are empty")
    return _sha(json.dumps(files, separators=(",", ":")).encode())


def _drop_prefix() -> list[str]:
    return [
        "/usr/bin/setpriv",
        "--no-new-privs",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--",
    ]


def _kill(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _browser(
    executable: str,
    base_url: str,
    scenario: str,
    profile: Path,
    extension: Path,
    expected_text: str,
) -> bytes:
    command = [
        *_drop_prefix(),
        executable,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        f"--disable-extensions-except={extension}",
        f"--load-extension={extension}",
        f"--user-data-dir={profile}",
        "--virtual-time-budget=5000",
        "--dump-dom",
        f"{base_url}/?safe-update-probe={quote(scenario)}",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, _stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired as exc:
        _kill(process)
        raise RuntimeError(f"browser scenario timed out: {scenario}") from exc
    if process.returncode or len(stdout) > 4 * 1024 * 1024:
        raise RuntimeError(f"browser scenario failed: {scenario}")
    if (
        b"<title>Proxima" not in stdout
        or not re.search(br"""id=["']root["']""", stdout)
        or expected_text.encode() not in stdout
    ):
        raise RuntimeError(f"browser scenario rendered an invalid shell: {scenario}")
    return stdout


def _sse(host: str, port: int, session_id: int, token: str) -> bytes:
    connection = http.client.HTTPConnection(host, port, timeout=10)
    connection.request(
        "GET",
        f"/api/sessions/{session_id}/events/stream?token={quote(token)}",
    )
    response = connection.getresponse()
    if response.status != 200 or "text/event-stream" not in str(response.getheader("content-type")):
        connection.close()
        raise RuntimeError("candidate SSE probe failed")
    line = response.readline()
    connection.close()
    if line not in {b": connected\n", b": connected\r\n"}:
        raise RuntimeError("candidate SSE handshake is invalid")
    return line


def _main(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    subprocess.run(
        ["/usr/sbin/ip", "link", "set", "lo", "up"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=5,
    )
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
    server_log = config_path.parent / "candidate-server.log"
    server_output = server_log.open("wb")
    server = subprocess.Popen(
        [*_drop_prefix(), *config["server_argv"]],
        cwd=config["server_cwd"],
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        stdout=server_output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        base_url = config["base_url"]
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("candidate server exited before readiness")
            try:
                health, health_raw = _json(f"{base_url}/api/health")
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)
        else:
            raise RuntimeError("candidate server readiness timed out") from last_error
        expected = config["identity"]
        for name in ("release_id", "commit", "asset_manifest_digest", "version"):
            if health.get(name) != expected[name]:
                raise RuntimeError(f"candidate {name} identity mismatch")
        maintenance, maintenance_raw = _json(
            f"{base_url}/api/maintenance",
            config["auth_token"],
        )
        if maintenance.get("active") is not False:
            raise RuntimeError("candidate maintenance state is not inert")
        index, content_type = _bytes(f"{base_url}/")
        if "text/html" not in content_type:
            raise RuntimeError("candidate browser shell content type is invalid")
        references = re.findall(rb"""(?:src|href)=["'](/assets/[^"'?#]+)""", index)
        if not references:
            raise RuntimeError("candidate shell has no served asset")
        asset_path = references[0].decode()
        served_asset, _asset_type = _bytes(f"{base_url}{asset_path}")
        local_asset = (Path(config["web_dist"]) / asset_path.removeprefix("/")).resolve()
        web_dist = Path(config["web_dist"]).resolve()
        if web_dist not in local_asset.parents or served_asset != local_asset.read_bytes():
            raise RuntimeError("candidate served asset differs from frozen release")
        if _asset_digest(web_dist) != expected["asset_manifest_digest"]:
            raise RuntimeError("candidate static manifest digest mismatch")
        sse = _sse(
            "127.0.0.1",
            int(config["port"]),
            int(config["session_id"]),
            config["auth_token"],
        )
        scenarios = json.loads(Path(config["browser_scenarios"]).read_text(encoding="utf-8"))
        names = scenarios.get("scenarios") if isinstance(scenarios, dict) else None
        if (
            scenarios.get("version") != 1
            or not isinstance(names, list)
            or not names
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
        ):
            raise RuntimeError("trusted browser scenario manifest is invalid")
        browser_results: dict[str, str] = {}
        browser_root = Path(config["browser_profile"])
        browser_root.mkdir(mode=0o700)
        extension = browser_root / "auth-extension"
        extension.mkdir(mode=0o700)
        (extension / "manifest.json").write_text(
            json.dumps(
                {
                    "content_scripts": [
                        {
                            "js": ["session.js"],
                            "matches": ["http://127.0.0.1/*"],
                            "run_at": "document_start",
                        }
                    ],
                    "manifest_version": 3,
                    "name": "Proxima trusted candidate probe",
                    "version": "1.0",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        (extension / "session.js").write_text(
            "document.cookie="
            + json.dumps(
                f"proxima_session={config['auth_token']}; Path=/; SameSite=Lax"
            )
            + ";\n",
            encoding="utf-8",
        )
        for position, name in enumerate(names):
            dom = _browser(
                config["browser_executable"],
                base_url,
                name,
                browser_root / str(position),
                extension,
                config["browser_expected_text"],
            )
            browser_results[name] = _sha(dom)
        return {
            "api": _sha(health_raw),
            "authenticated": _sha(maintenance_raw),
            "browser": browser_results,
            "served_asset": _sha(served_asset),
            "sse": _sha(sse),
            "static_manifest": expected["asset_manifest_digest"],
            "version": expected["version"],
        }
    finally:
        if server.poll() is None:
            _kill(server)
        server_output.close()


if __name__ == "__main__":
    try:
        result = _main(Path(sys.argv[1]))
    except Exception as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "ok": False},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(1)
    print(json.dumps({"ok": True, "results": result}, sort_keys=True, separators=(",", ":")))
