"""Canonical host-platform support contract.

The server host determines install, service-manager, PTY, diagnostics, preview,
and backup behavior. Browser clients remain ordinary web clients, but the
Linux-first daily-driver claim is deliberately narrower than "the app starts".
Keep user-facing labels sourced from this module so documentation and the UI do
not silently promote experimental packaging to supported status.
"""
from __future__ import annotations

import platform
from typing import Any


_PLATFORMS: tuple[dict[str, str], ...] = (
    {
        "key": "linux",
        "label": "Linux",
        "tier": "supported",
        "summary": (
            "Daily-driver server and browser client support, including systemd, "
            "PTY terminals, backups, diagnostics, preview, and Tailscale access."
        ),
    },
    {
        "key": "macos",
        "label": "macOS",
        "tier": "experimental",
        "summary": (
            "LaunchAgent packaging is available, but the complete Linux "
            "daily-driver acceptance matrix is not yet qualified."
        ),
    },
    {
        "key": "windows",
        "label": "Windows",
        "tier": "experimental",
        "summary": (
            "Scheduled Task packaging is available, but PTY and the complete "
            "Linux daily-driver acceptance matrix are not yet qualified."
        ),
    },
)

_UNKNOWN = {
    "key": "unsupported",
    "label": "Unsupported platform",
    "tier": "unsupported",
    "summary": (
        "Proxima has no qualified installer or service lifecycle for this host. "
        "Use a supported Linux host or an explicitly experimental package."
    ),
}


def platform_key(system: str | None = None) -> str:
    """Normalize ``platform.system()`` without treating unknown hosts as Linux."""
    name = (platform.system() if system is None else system).strip().lower()
    if name == "linux":
        return "linux"
    if name == "darwin":
        return "macos"
    if name.startswith("win"):
        return "windows"
    return "unsupported"


def current_platform(system: str | None = None) -> dict[str, str]:
    key = platform_key(system)
    for item in _PLATFORMS:
        if item["key"] == key:
            return dict(item)
    return dict(_UNKNOWN)


def support_payload(system: str | None = None) -> dict[str, Any]:
    """Return the public, nonsecret support catalog and current server tier."""
    return {
        "claim": "linux-first-daily-driver",
        "server": current_platform(system),
        "platforms": [dict(item) for item in _PLATFORMS],
        "reference": "docs/linux-daily-driver-acceptance.md",
    }
