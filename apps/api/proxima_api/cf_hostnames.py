"""Cloudflare API: per-app preview hostnames.

When a project app starts, we expose it at `<slug>.<apps_domain>` by (idempotently):
  1. adding a tunnel ingress rule  <hostname> → the main app service,
  2. creating a proxied DNS CNAME   <hostname> → <tunnel-id>.cfargotunnel.com,
  3. creating a self-hosted Access app for <hostname> with the owner-email policy
     (so the preview is NOT public — same gate as the main app).
On app stop we remove all three. All calls are no-ops if `apps_domain`/`cf_*` config
is missing, so a deployment without Cloudflare creds just skips remote previews.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

_LOG = logging.getLogger("proxima.cf_hostnames")
_API = "https://api.cloudflare.com/client/v4"
_FALLBACK_SERVICE = "http://127.0.0.1:8766"


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


async def _owner_emails(cfg, client) -> list[str]:
    """Emails allowed on the existing (main-app) Access apps — so previews inherit
    the same allow-list instead of hard-coding it."""
    r = await client.get(f"{_API}/accounts/{cfg['cf_account_id']}/access/apps")
    r.raise_for_status()
    emails: list[str] = []
    for app in (r.json().get("result") or []):
        for pol in (app.get("policies") or []):
            if pol.get("decision") == "allow":
                for inc in (pol.get("include") or []):
                    e = (inc.get("email") or {}).get("email")
                    if e and e not in emails:
                        emails.append(e)
    return emails


async def ensure_preview_hostname(cfg: dict[str, Any], slug: str) -> None:
    if not configured(cfg):
        return
    host = hostname_for(cfg, slug)
    async with httpx.AsyncClient(timeout=20, headers=_headers(cfg)) as client:
        # 1. Tunnel ingress rule (insert before the catch-all).
        config = await _tunnel_config(cfg, client)
        ingress = config.get("ingress") or [{"service": "http_status:404"}]
        if not any(r.get("hostname") == host for r in ingress):
            service = _existing_service(ingress)
            catchall = ingress[-1:] if ingress and not ingress[-1].get("hostname") else [{"service": "http_status:404"}]
            body = [r for r in ingress if r.get("hostname")]
            body.append({"hostname": host, "service": service})
            config["ingress"] = body + catchall
            await _put_tunnel_config(cfg, client, config)

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
            config = await _tunnel_config(cfg, client)
            ingress = config.get("ingress") or []
            if any(r.get("hostname") == host for r in ingress):
                config["ingress"] = [r for r in ingress if r.get("hostname") != host]
                await _put_tunnel_config(cfg, client, config)
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
