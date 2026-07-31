from __future__ import annotations

import asyncio
import copy
import json as jsonlib
import multiprocessing
import os
import threading
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
                {
                    "path": "/internal/*",
                    "service": "http://127.0.0.1:9000",
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

    asyncio.run(
        cf_hostnames._mutate_tunnel_ingress(
            cfg,
            client,
            lambda ingress: cf_hostnames._with_ingress_hostname(
                ingress,
                host,
            ),
            lambda ingress: any(
                rule.get("hostname") == host for rule in ingress
            ),
        )
    )


async def _assert_lock_reusable(cfg: dict) -> None:
    async def acquire() -> None:
        async with cf_hostnames._tunnel_mutation_lock(cfg):
            pass

    await asyncio.wait_for(acquire(), timeout=2)


def test_add_host_preserves_path_rules_and_terminal_catchall() -> None:
    ingress = [
        {
            "hostname": "preview-existing.example.test",
            "service": "http://127.0.0.1:8766",
        },
        {
            "path": "/internal/*",
            "service": "http://127.0.0.1:9000",
        },
        {
            "service": "http_status:404",
            "originRequest": {"connectTimeout": 5},
        },
    ]

    updated = cf_hostnames._with_ingress_hostname(
        ingress,
        "file-1-ops-2.example.test",
    )

    assert updated == [
        ingress[0],
        {
            "hostname": "file-1-ops-2.example.test",
            "service": "http://127.0.0.1:8766",
        },
        ingress[1],
        ingress[-1],
    ]
    assert ingress[-1]["originRequest"] == {"connectTimeout": 5}


def test_existing_file_host_moves_before_path_matcher() -> None:
    host = "file-1-ops-2.example.test"
    ingress = [
        {
            "hostname": "preview-existing.example.test",
            "service": "http://127.0.0.1:8766",
        },
        {
            "path": "/internal/*",
            "service": "http://127.0.0.1:9000",
        },
        {
            "hostname": host,
            "service": "http://127.0.0.1:8766",
            "originRequest": {"connectTimeout": 5},
        },
        {"service": "http_status:404"},
    ]

    updated = cf_hostnames._with_ingress_hostname(ingress, host)

    assert [rule.get("hostname") for rule in updated[:2]] == [
        "preview-existing.example.test",
        host,
    ]
    assert updated[1]["originRequest"] == {"connectTimeout": 5}
    assert updated[2:] == [ingress[1], ingress[3]]
    assert cf_hostnames._hostname_precedes_unscoped(updated, host)


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
        await cf_hostnames._mutate_tunnel_ingress(
            cfg,
            client,
            lambda ingress: cf_hostnames._with_ingress_hostname(
                ingress,
                host,
            ),
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
    assert client.config["ingress"][0]["hostname"] == (
        "preview-existing.example.test"
    )
    assert {
        rule["hostname"]
        for rule in client.config["ingress"][1:3]
    } == set(hosts)
    assert client.config["ingress"][3] == {
        "path": "/internal/*",
        "service": "http://127.0.0.1:9000",
    }
    assert client.config["ingress"][-1] == {
        "service": "http_status:404"
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
                    {
                        "path": "/internal/*",
                        "service": "http://127.0.0.1:9000",
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
    final_ingress = jsonlib.loads(
        state_path.read_text(encoding="utf-8")
    )["ingress"]
    assert final_ingress[0]["hostname"] == (
        "preview-existing.example.test"
    )
    assert {
        rule["hostname"]
        for rule in final_ingress[1:3]
    } == set(hosts)
    assert final_ingress[3] == {
        "path": "/internal/*",
        "service": "http://127.0.0.1:9000",
    }
    assert final_ingress[-1] == {"service": "http_status:404"}


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

    with pytest.raises(
        RuntimeError,
        match="tunnel ingress update did not converge",
    ):
        asyncio.run(
            cf_hostnames._mutate_tunnel_ingress(
                cfg,
                client,
                lambda ingress: cf_hostnames._with_ingress_hostname(
                    ingress,
                    host,
                ),
                lambda ingress: any(
                    rule.get("hostname") == host for rule in ingress
                ),
            )
        )


def test_cancelled_waiter_releases_late_file_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = {
        "cf_account_id": "account",
        "cf_tunnel_id": "cancelled-waiter-test",
        "cf_ingress_lock_dir": str(tmp_path),
    }
    holder = cf_hostnames._acquire_ingress_file_lock(cfg)
    original_acquire = cf_hostnames._acquire_ingress_file_lock
    attempting = threading.Event()

    def observed_acquire(config):
        attempting.set()
        return original_acquire(config)

    monkeypatch.setattr(
        cf_hostnames,
        "_acquire_ingress_file_lock",
        observed_acquire,
    )

    async def run() -> None:
        async def wait_for_lock() -> None:
            async with cf_hostnames._tunnel_mutation_lock(cfg):
                raise AssertionError("cancelled waiter entered the lock")

        task = asyncio.create_task(wait_for_lock())
        holder_released = False
        task_collected = False
        try:
            assert await asyncio.to_thread(attempting.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            cf_hostnames._release_ingress_file_lock(holder)
            holder_released = True
            with pytest.raises(asyncio.CancelledError):
                await task
            task_collected = True
            await _assert_lock_reusable(cfg)
        finally:
            if not holder_released:
                cf_hostnames._release_ingress_file_lock(holder)
            if not task_collected:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, AssertionError):
                    pass

    asyncio.run(run())


def test_cancelled_owner_releases_acquired_file_lock(
    tmp_path: Path,
) -> None:
    cfg = {
        "cf_account_id": "account",
        "cf_tunnel_id": "cancelled-owner-test",
        "cf_ingress_lock_dir": str(tmp_path),
    }

    async def run() -> None:
        entered = asyncio.Event()

        async def own_lock() -> None:
            async with cf_hostnames._tunnel_mutation_lock(cfg):
                entered.set()
                await asyncio.Future()

        task = asyncio.create_task(own_lock())
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_lock_reusable(cfg)

    asyncio.run(run())
