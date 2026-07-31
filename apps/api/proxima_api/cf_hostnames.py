"""Cloudflare API: isolated preview hostnames.

Preview origins are exposed under `<apps_domain>` by:
  1. adding a tunnel ingress rule  <hostname> → the main app service,
  2. creating a proxied DNS CNAME   <hostname> → <tunnel-id>.cfargotunnel.com,
  3. removing any stale per-host Cloudflare Access app, because embedded previews
     authenticate with Proxima's short-lived preview cookie instead.
App hosts are removed on app stop. File Area hosts remain inert if their
database binding disappears. All calls are no-ops if
`apps_domain`/`cf_*` config is missing.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import stat
import tempfile
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx

_LOG = logging.getLogger("proxima.cf_hostnames")
_API = "https://api.cloudflare.com/client/v4"
_FALLBACK_SERVICE = "http://127.0.0.1:8766"
_INGRESS_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _owner_id() -> int:
    return int(getattr(os, "getuid", lambda: 0)())


def configured(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("apps_domain") and cfg.get("cf_api_token")
               and cfg.get("cf_account_id") and cfg.get("cf_tunnel_id")
               and cfg.get("cf_zone_id"))


def hostname_for(cfg: dict[str, Any], slug: str) -> str:
    # `preview-<slug>` is one DNS label under the zone, so a 1-level apps_domain
    # (e.g. example.com) keeps hostnames covered by the free Universal SSL cert
    # (`*.example.com`) — no ACM / Total TLS needed. The `preview-` prefix also
    # namespaces them away from real subdomains.
    return f"preview-{slug}.{cfg['apps_domain']}"


def file_preview_hostname_for(
    cfg: dict[str, Any],
    project_id: int,
    area_kind: str,
    area_id: int | None,
) -> str:
    return (
        f"file-{project_id}-{area_kind}-{area_id or 0}."
        f"{cfg['apps_domain']}"
    )


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg['cf_api_token']}", "Content-Type": "application/json"}


async def _tunnel_config(cfg, client) -> dict[str, Any]:
    r = await client.get(f"{_API}/accounts/{cfg['cf_account_id']}/cfd_tunnel/{cfg['cf_tunnel_id']}/configurations")
    r.raise_for_status()
    return (r.json().get("result") or {}).get("config") or {"ingress": [{"service": "http_status:404"}]}


def _existing_service(ingress: list[dict[str, Any]]) -> str:
    for rule in ingress:
        if rule.get("hostname") and rule.get("service"):
            return rule["service"]
    return _FALLBACK_SERVICE


async def _put_tunnel_config(cfg, client, config: dict[str, Any]) -> None:
    r = await client.put(
        f"{_API}/accounts/{cfg['cf_account_id']}/cfd_tunnel/{cfg['cf_tunnel_id']}/configurations",
        json={"config": config},
    )
    r.raise_for_status()


def _ingress_lock(cfg: dict[str, Any]) -> asyncio.Lock:
    key = (str(cfg["cf_account_id"]), str(cfg["cf_tunnel_id"]))
    return _INGRESS_LOCKS.setdefault(key, asyncio.Lock())


def _ingress_lock_path(cfg: dict[str, Any]) -> Path:
    root = Path(
        str(
            cfg.get("cf_ingress_lock_dir")
            or os.environ.get("XDG_RUNTIME_DIR")
            or tempfile.gettempdir()
        )
    )
    digest = hashlib.sha256(
        f"{cfg['cf_account_id']}:{cfg['cf_tunnel_id']}".encode()
    ).hexdigest()
    return root / f".proxima-cf-ingress-{_owner_id()}-{digest}.lock"


def _acquire_ingress_file_lock(cfg: dict[str, Any]) -> int:
    path = _ingress_lock_path(cfg)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (
                hasattr(metadata, "st_uid")
                and metadata.st_uid != _owner_id()
            )
        ):
            raise RuntimeError("Cloudflare ingress lock is unsafe")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        elif os.name == "nt":
            import msvcrt

            if metadata.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            raise RuntimeError("cross-process file locking is unavailable")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _release_ingress_file_lock(descriptor: int) -> None:
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


@asynccontextmanager
async def _tunnel_mutation_lock(
    cfg: dict[str, Any],
) -> AsyncIterator[None]:
    async with _ingress_lock(cfg):
        descriptor = await asyncio.to_thread(
            _acquire_ingress_file_lock,
            cfg,
        )
        try:
            yield
        finally:
            await asyncio.to_thread(
                _release_ingress_file_lock,
                descriptor,
            )


def _ingress_fingerprint(rule: dict[str, Any]) -> str:
    return json.dumps(rule, separators=(",", ":"), sort_keys=True)


def _preserves_ingress(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
) -> bool:
    actual_rules = Counter(_ingress_fingerprint(rule) for rule in actual)
    expected_rules = Counter(_ingress_fingerprint(rule) for rule in expected)
    return not expected_rules - actual_rules


async def _mutate_tunnel_ingress(
    cfg: dict[str, Any],
    client,
    mutate: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    complete: Callable[[list[dict[str, Any]]], bool],
) -> None:
    async with _tunnel_mutation_lock(cfg):
        config = await _tunnel_config(cfg, client)
        ingress = config.get("ingress") or [{"service": "http_status:404"}]
        if complete(ingress):
            return
        updated = mutate(copy.deepcopy(ingress))
        next_config = {**config, "ingress": updated}
        await _put_tunnel_config(cfg, client, next_config)
        for _ in range(5):
            refreshed = await _tunnel_config(cfg, client)
            refreshed_ingress = refreshed.get("ingress") or []
            if (
                complete(refreshed_ingress)
                and _preserves_ingress(refreshed_ingress, updated)
            ):
                return
            await asyncio.sleep(0)
        raise RuntimeError("Cloudflare tunnel ingress update did not converge")


async def _ensure_hostname(cfg: dict[str, Any], host: str) -> None:
    if not configured(cfg):
        return
    async with httpx.AsyncClient(timeout=20, headers=_headers(cfg)) as client:
        def add_host(ingress: list[dict[str, Any]]) -> list[dict[str, Any]]:
            service = _existing_service(ingress)
            catchall = ingress[-1:] if ingress and not ingress[-1].get("hostname") else [{"service": "http_status:404"}]
            body = [r for r in ingress if r.get("hostname")]
            body.append({"hostname": host, "service": service})
            return body + catchall

        await _mutate_tunnel_ingress(
            cfg,
            client,
            add_host,
            lambda ingress: any(r.get("hostname") == host for r in ingress),
        )

        # 2. Proxied DNS CNAME → the tunnel.
        got = await client.get(f"{_API}/zones/{cfg['cf_zone_id']}/dns_records", params={"name": host})
        got.raise_for_status()
        if not (got.json().get("result") or []):
            made = await client.post(
                f"{_API}/zones/{cfg['cf_zone_id']}/dns_records",
                json={"type": "CNAME", "name": host, "content": f"{cfg['cf_tunnel_id']}.cfargotunnel.com",
                      "proxied": True, "ttl": 1},
            )
            made.raise_for_status()

        # 3. Ensure NO Cloudflare Access app on this host. Previews must be iframable
        # (Access can't finish its login inside a frame), so instead of a CF gate they
        # are gated by the app's `proxima_preview` cookie in preview_proxy. Delete any stale
        # per-host Access app left from the earlier design.
        apps = (await client.get(f"{_API}/accounts/{cfg['cf_account_id']}/access/apps")).json().get("result") or []
        for a in apps:
            if a.get("domain") == host:
                await client.delete(f"{_API}/accounts/{cfg['cf_account_id']}/access/apps/{a['id']}")


async def ensure_preview_hostname(cfg: dict[str, Any], slug: str) -> None:
    await _ensure_hostname(cfg, hostname_for(cfg, slug))


async def ensure_file_preview_hostname(
    cfg: dict[str, Any],
    project_id: int,
    area_kind: str,
    area_id: int | None,
) -> None:
    await _ensure_hostname(
        cfg,
        file_preview_hostname_for(
            cfg,
            project_id,
            area_kind,
            area_id,
        ),
    )


async def provision(cfg: dict[str, Any], slug: str) -> None:
    """Fire-and-forget-safe wrapper for app start (never raises)."""
    try:
        await ensure_preview_hostname(cfg, slug)
    except Exception:
        _LOG.exception("preview hostname provision failed for %s", slug)


async def deprovision(cfg: dict[str, Any], slug: str) -> None:
    try:
        await remove_preview_hostname(cfg, slug)
    except Exception:
        _LOG.exception("preview hostname deprovision failed for %s", slug)


async def remove_preview_hostname(cfg: dict[str, Any], slug: str) -> None:
    if not configured(cfg):
        return
    host = hostname_for(cfg, slug)
    async with httpx.AsyncClient(timeout=20, headers=_headers(cfg)) as client:
        try:
            await _mutate_tunnel_ingress(
                cfg,
                client,
                lambda ingress: [
                    rule for rule in ingress if rule.get("hostname") != host
                ],
                lambda ingress: not any(
                    rule.get("hostname") == host for rule in ingress
                ),
            )
        except Exception:
            _LOG.exception("remove tunnel ingress failed for %s", host)
        try:
            got = await client.get(f"{_API}/zones/{cfg['cf_zone_id']}/dns_records", params={"name": host})
            for rec in (got.json().get("result") or []):
                await client.delete(f"{_API}/zones/{cfg['cf_zone_id']}/dns_records/{rec['id']}")
        except Exception:
            _LOG.exception("remove DNS failed for %s", host)
        try:
            apps = (await client.get(f"{_API}/accounts/{cfg['cf_account_id']}/access/apps")).json().get("result") or []
            for a in apps:
                if a.get("domain") == host:
                    await client.delete(f"{_API}/accounts/{cfg['cf_account_id']}/access/apps/{a['id']}")
        except Exception:
            _LOG.exception("remove Access app failed for %s", host)
