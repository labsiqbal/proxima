from __future__ import annotations

import asyncio
import copy
import json as jsonlib
import multiprocessing
import os
from pathlib import Path

import pytest

from proxima_api import cf_hostnames


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _TunnelClient:
    def __init__(self) -> None:
        self.config = {
            "ingress": [
                {
                    "hostname": "preview-existing.example.test",
                    "service": "http://127.0.0.1:8766",
                },
                {"service": "http_status:404"},
            ]
        }

    async def get(self, _url, **_kwargs):
        await asyncio.sleep(0)
        return _Response({"result": {"config": copy.deepcopy(self.config)}})

    async def put(self, _url, *, json):
        await asyncio.sleep(0)
        self.config = copy.deepcopy(json["config"])
        return _Response({"success": True})


class _FileTunnelClient:
    def __init__(self, path: str, host: str) -> None:
        self.path = Path(path)
        self.host = host
        self.get_count = 0

    async def get(self, _url, **_kwargs):
        config = jsonlib.loads(self.path.read_text(encoding="utf-8"))
        self.get_count += 1
        if self.get_count == 1:
            await asyncio.sleep(0.05)
        return _Response({"result": {"config": config}})

    async def put(self, _url, *, json):
        if self.host.startswith("file-slow"):
            await asyncio.sleep(0.2)
        else:
            await asyncio.sleep(0.01)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            jsonlib.dumps(json["config"]),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        return _Response({"success": True})


class _DroppingTunnelClient(_TunnelClient):
    async def put(self, _url, *, json):
        await asyncio.sleep(0)
        requested = next(
            rule
            for rule in json["config"]["ingress"]
            if str(rule.get("hostname") or "").startswith("file-")
        )
        self.config = {
            "ingress": [
                copy.deepcopy(requested),
                {"service": "http_status:404"},
            ]
        }
        return _Response({"success": True})


def _add_host_in_process(
    state_path: str,
    lock_dir: str,
    host: str,
    start,
) -> None:
    start.wait()
    cfg = {
        "cf_account_id": "account",
        "cf_tunnel_id": "multiprocess-preview-test",
        "cf_ingress_lock_dir": lock_dir,
    }
    client = _FileTunnelClient(state_path, host)

    def mutate(ingress):
        service = cf_hostnames._existing_service(ingress)
        catchall = (
            ingress[-1:]
            if ingress and not ingress[-1].get("hostname")
            else [{"service": "http_status:404"}]
        )
        rules = [rule for rule in ingress if rule.get("hostname")]
        rules.append({"hostname": host, "service": service})
        return rules + catchall

    asyncio.run(
        cf_hostnames._mutate_tunnel_ingress(
            cfg,
            client,
            mutate,
            lambda ingress: any(
                rule.get("hostname") == host for rule in ingress
            ),
        )
    )


def test_concurrent_file_preview_hosts_preserve_shared_tunnel_ingress(
    tmp_path: Path,
) -> None:
    cfg = {
        "cf_account_id": "account",
        "cf_tunnel_id": "concurrent-preview-test",
        "cf_ingress_lock_dir": str(tmp_path),
    }
    client = _TunnelClient()
    hosts = (
        "file-1-ops-2.example.test",
        "file-1-code-3.example.test",
    )

    async def add(host: str) -> None:
        def mutate(ingress):
            service = cf_hostnames._existing_service(ingress)
            catchall = (
                ingress[-1:]
                if ingress and not ingress[-1].get("hostname")
                else [{"service": "http_status:404"}]
            )
            rules = [rule for rule in ingress if rule.get("hostname")]
            rules.append({"hostname": host, "service": service})
            return rules + catchall

        await cf_hostnames._mutate_tunnel_ingress(
            cfg,
            client,
            mutate,
            lambda ingress: any(
                rule.get("hostname") == host for rule in ingress
            ),
        )

    async def run() -> None:
        await asyncio.gather(*(add(host) for host in hosts))

    asyncio.run(run())

    configured_hosts = {
        rule.get("hostname")
        for rule in client.config["ingress"]
        if rule.get("hostname")
    }
    assert configured_hosts == {
        "preview-existing.example.test",
        *hosts,
    }


def test_multiprocess_file_preview_hosts_preserve_complete_ingress(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "tunnel.json"
    state_path.write_text(
        jsonlib.dumps(
            {
                "ingress": [
                    {
                        "hostname": "preview-existing.example.test",
                        "service": "http://127.0.0.1:8766",
                    },
                    {"service": "http_status:404"},
                ]
            }
        ),
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    hosts = (
        "file-slow-ops-1.example.test",
        "file-fast-code-2.example.test",
    )
    processes = [
        context.Process(
            target=_add_host_in_process,
            args=(str(state_path), str(tmp_path), host, start),
        )
        for host in hosts
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        assert process.exitcode == 0

    configured_hosts = {
        rule.get("hostname")
        for rule in jsonlib.loads(
            state_path.read_text(encoding="utf-8")
        )["ingress"]
        if rule.get("hostname")
    }
    assert configured_hosts == {
        "preview-existing.example.test",
        *hosts,
    }


def test_tunnel_update_rejects_a_refreshed_partial_ingress(
    tmp_path: Path,
) -> None:
    cfg = {
        "cf_account_id": "account",
        "cf_tunnel_id": "partial-preview-test",
        "cf_ingress_lock_dir": str(tmp_path),
    }
    client = _DroppingTunnelClient()
    host = "file-1-ops-2.example.test"

    def mutate(ingress):
        rules = [rule for rule in ingress if rule.get("hostname")]
        rules.append(
            {
                "hostname": host,
                "service": "http://127.0.0.1:8766",
            }
        )
        return rules + [{"service": "http_status:404"}]

    with pytest.raises(
        RuntimeError,
        match="tunnel ingress update did not converge",
    ):
        asyncio.run(
            cf_hostnames._mutate_tunnel_ingress(
                cfg,
                client,
                mutate,
                lambda ingress: any(
                    rule.get("hostname") == host for rule in ingress
                ),
            )
        )
