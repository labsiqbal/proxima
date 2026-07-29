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

from browser import run_scenario


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
        "--reuid=65534",
        "--regid=65534",
        "--clear-groups",
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
        definitions = scenarios.get("scenarios") if isinstance(scenarios, dict) else None
        required = {
            "focus",
            "graph-freshness",
            "login",
            "master-popup-home",
            "ops-task",
            "repo-task",
            "review",
            "update-status",
        }
        if (
            scenarios.get("version") != 2
            or not isinstance(definitions, list)
            or not definitions
        ):
            raise RuntimeError("trusted browser scenario manifest is invalid")
        names: list[str] = []
        for scenario in definitions:
            if (
                not isinstance(scenario, dict)
                or set(scenario) != {"authenticated", "name", "steps"}
                or not isinstance(scenario["name"], str)
                or not isinstance(scenario["authenticated"], bool)
                or not isinstance(scenario["steps"], list)
                or not scenario["steps"]
            ):
                raise RuntimeError("trusted browser scenario manifest is invalid")
            names.append(scenario["name"])
            for step in scenario["steps"]:
                if (
                    not isinstance(step, dict)
                    or not set(step).issubset(
                        {"action", "selector", "text", "timeout", "value"}
                    )
                    or set(step) < {"action", "selector"}
                    or step["action"] not in {"assert", "click", "fill", "select"}
                    or not isinstance(step["selector"], str)
                    or not step["selector"]
                    or len(step["selector"]) > 256
                    or (
                        "text" in step
                        and (
                            not isinstance(step["text"], str)
                            or len(step["text"]) > 256
                        )
                    )
                    or (
                        step["action"] in {"fill", "select"}
                        and not isinstance(step.get("value"), str)
                    )
                    or (
                        "timeout" in step
                        and (
                            not isinstance(step["timeout"], int)
                            or isinstance(step["timeout"], bool)
                            or not 1 <= step["timeout"] <= 30
                        )
                    )
                ):
                    raise RuntimeError("trusted browser scenario manifest is invalid")
        if set(names) != required or len(names) != len(set(names)):
            raise RuntimeError("trusted browser scenario manifest is incomplete")
        browser_results: dict[str, str] = {}
        browser_root = Path(config["browser_profile"])
        browser_root.mkdir(mode=0o777)
        browser_root.chmod(0o777)
        for position, scenario in enumerate(definitions):
            transcript = run_scenario(
                executable=config["browser_executable"],
                base_url=base_url,
                scenario=scenario,
                profile=browser_root / str(position),
                auth_token=config["auth_token"],
                drop_prefix=_drop_prefix(),
            )
            browser_results[scenario["name"]] = _sha(transcript)
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
