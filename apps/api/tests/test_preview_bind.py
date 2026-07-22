"""LAN-hardening regressions for the preview surface (audit F1 + F2).

The relay's default bind must never be 0.0.0.0: "auto" resolves to the tailnet
interface when the host has one, else loopback, and only an explicit
PROXIMA_PREVIEW_BIND can widen that. Separately, a dev server that binds beyond
loopback is directly reachable by LAN/tailnet devices with no auth (the gated
relay does not protect it), so the app runner must detect and surface it.
"""
from __future__ import annotations

import contextlib
import ipaddress
import socket

import pytest

from proxima_api import preview_proxy
from proxima_api.apprunner import port_bound_non_loopback
from proxima_api.preview_proxy import (
    PreviewRelayManager,
    TAILNET_IPV4_NET,
    resolve_preview_bind_host,
    tailnet_address,
)


def test_resolve_auto_prefers_tailnet_address(monkeypatch):
    monkeypatch.setattr(preview_proxy, "tailnet_address", lambda: "100.101.102.103")
    assert resolve_preview_bind_host("auto") == "100.101.102.103"
    assert resolve_preview_bind_host("AUTO") == "100.101.102.103"


def test_resolve_auto_falls_back_to_loopback_never_wildcard(monkeypatch):
    monkeypatch.setattr(preview_proxy, "tailnet_address", lambda: None)
    assert resolve_preview_bind_host("auto") == "127.0.0.1"


def test_resolve_respects_explicit_overrides(monkeypatch):
    # An operator's explicit choice passes through untouched — including the
    # broad bind, which must be a conscious opt-in, and "off".
    monkeypatch.setattr(preview_proxy, "tailnet_address", lambda: "100.101.102.103")
    assert resolve_preview_bind_host("0.0.0.0") == "0.0.0.0"
    assert resolve_preview_bind_host("192.168.1.50") == "192.168.1.50"
    assert resolve_preview_bind_host("127.0.0.1") == "127.0.0.1"
    assert resolve_preview_bind_host("off") == "off"
    assert resolve_preview_bind_host("") == ""
    assert resolve_preview_bind_host(None) == ""


def test_relay_manager_resolves_auto(monkeypatch):
    monkeypatch.setattr(preview_proxy, "tailnet_address", lambda: "100.101.102.103")
    relays = PreviewRelayManager("auto", port_for=lambda slug: None)
    assert relays.enabled and relays.bind_host == "100.101.102.103"

    monkeypatch.setattr(preview_proxy, "tailnet_address", lambda: None)
    relays = PreviewRelayManager("auto", port_for=lambda slug: None)
    assert relays.enabled and relays.bind_host == "127.0.0.1"

    assert PreviewRelayManager("off", port_for=lambda slug: None).enabled is False


def test_tailnet_address_is_cgnat_or_absent():
    # Environment-dependent by nature: whatever it returns must be a real
    # tailnet (CGNAT-range) address — anything else must come back as None.
    addr = tailnet_address()
    if addr is not None:
        assert ipaddress.ip_address(addr) in TAILNET_IPV4_NET


@contextlib.contextmanager
def _listener(host: str):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        sock.listen(1)
        yield int(sock.getsockname()[1])
    finally:
        sock.close()


def test_port_bound_non_loopback_detection():
    with _listener("127.0.0.1") as port:
        result = port_bound_non_loopback(port)
        if result is None:
            pytest.skip("/proc/net not available on this platform")
        assert result is False
    with _listener("0.0.0.0") as port:
        assert port_bound_non_loopback(port) is True
