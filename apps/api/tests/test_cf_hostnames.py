from __future__ import annotations

import asyncio
import copy

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


def test_concurrent_file_preview_hosts_preserve_shared_tunnel_ingress() -> None:
    cfg = {
        "cf_account_id": "account",
        "cf_tunnel_id": "concurrent-preview-test",
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
