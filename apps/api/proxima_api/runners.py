from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .runner_specs import RUNNER_SPECS, runner_binary_names, selectable_runner_specs


# Keep child processes usable without handing them every secret carried by the
# Proxima service. Provider credentials are intentionally allowed for agent
# runners because some supported CLIs authenticate through environment variables;
# app previews do not receive them. Owners can add names through the documented
# allowlist or explicitly restore legacy inheritance when a trusted setup needs it.
_SUBPROCESS_BASE_ENV = {
    "APPDATA", "COLORTERM", "COMSPEC", "FORCE_COLOR", "HOME", "LANG",
    "LC_ALL", "LC_CTYPE", "LOCALAPPDATA", "LOGNAME", "NO_COLOR", "PATH",
    "PATHEXT", "SHELL", "SYSTEMROOT", "TEMP", "TERM", "TMP", "TMPDIR",
    "USER", "USERPROFILE", "WINDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
}
_RUNNER_PROVIDER_ENV = {
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
    "OPENROUTER_API_KEY", "XAI_API_KEY",
}


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def subprocess_env(
    *,
    provider_auth: bool = False,
    allowlist_env: str | None = None,
    inherit_env: str | None = None,
) -> dict[str, str]:
    """Build a least-surprise environment for an untrusted child process.

    This is deliberately a lightweight self-hosted guardrail, not an OS sandbox:
    it prevents accidental leakage of unrelated service credentials while keeping
    PATH, locale, temp, and platform variables required by normal CLIs. An owner
    may opt specific variables in, or restore the old full inheritance explicitly.
    """
    if inherit_env and _env_on(inherit_env):
        env = os.environ.copy()
    else:
        names = set(_SUBPROCESS_BASE_ENV)
        if provider_auth:
            names.update(_RUNNER_PROVIDER_ENV)
        if allowlist_env:
            names.update(
                part.strip()
                for part in os.environ.get(allowlist_env, "").split(",")
                if part.strip()
            )
        env = {name: value for name, value in os.environ.items() if name in names}
    env["PATH"] = augmented_path(env.get("PATH"))
    return env


@dataclass(frozen=True)
class RunnerDefinition:
    id: str
    display_name: str
    binary_names: tuple[str, ...]
    has_adapter: bool
    detection_only: bool = False
    notes: str = ""


# Proxima runner registry. Runner capability now has one source of truth:
# runner_specs.RUNNER_SPECS. This module only translates that model into
# detection/readiness payloads.
RUNNER_REGISTRY: tuple[RunnerDefinition, ...] = tuple(
    RunnerDefinition(
        spec.id,
        spec.display_name,
        runner_binary_names(spec),
        spec.has_adapter,
        detection_only=spec.detection_only,
        notes=spec.notes,
    )
    for spec in RUNNER_SPECS.values()
)


def compat_shim_root() -> Path:
    """Workspace-local directory for host-compat command shims.

    Lives under PROXIMA_WORKSPACE_ROOT (default ~/.local/share/proxima) so shims
    stay with runtime data and never touch the owner's real ~/.local/bin.
    """
    root = os.environ.get("PROXIMA_WORKSPACE_ROOT") or str(Path.home() / ".local" / "share" / "proxima")
    return Path(root) / "shims"


def ensure_python_compat_shim(path_env: str) -> str | None:
    """If `python` is missing but `python3` exists, expose `python` via a shim dir.

    Many Linux hosts (Debian/Ubuntu, Nix-adjacent setups) ship only `python3`.
    Agent plan steps and project scripts still invoke `python`, which then fails
    with command-not-found even though the interpreter is installed. Return the
    shim directory to prepend, or None when no shim is needed/possible.
    """
    if os.name == "nt":
        return None
    if shutil.which("python", path=path_env):
        return None
    python3 = shutil.which("python3", path=path_env)
    if not python3:
        return None

    shim_dir = compat_shim_root()
    try:
        shim_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    target = shim_dir / "python"
    try:
        real_py3 = os.path.realpath(python3)
        if target.is_symlink() or target.exists():
            if target.is_symlink() and os.path.realpath(target) == real_py3:
                return str(shim_dir)
            target.unlink(missing_ok=True)
        target.symlink_to(real_py3)
        return str(shim_dir)
    except OSError:
        try:
            target.write_text(f"#!/bin/sh\nexec '{python3}' \"$@\"\n", encoding="utf-8")
            target.chmod(0o755)
            return str(shim_dir)
        except OSError:
            return None


def augmented_path(path_env: str | None = None) -> str:
    """PATH used by non-interactive service processes.

    GUI/server processes often miss Homebrew/user-local bins. Add the common
    local paths without replacing the provided environment. When the host has
    python3 but not python, prepend a workspace shim so agent/app subprocesses
    can still run `python`.
    """

    base = path_env or os.environ.get("PATH", "")
    if os.name == "nt":  # Windows: common npm/global bin dirs service procs miss
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        extras = tuple(p for p in (
            os.path.join(appdata, "npm") if appdata else "",
            os.path.join(local, "Microsoft", "WindowsApps") if local else "",
        ) if p)
    else:
        extras = (
            os.path.expanduser("~/.local/bin"),
            os.path.expanduser("~/bin"),
            "/home/linuxbrew/.linuxbrew/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
        )
    parts = [p for p in base.split(os.pathsep) if p]
    for extra in extras:
        if extra not in parts:
            parts.append(extra)
    # Build the candidate path first so the python probe does not see our shim.
    candidate = os.pathsep.join(parts)
    shim_dir = ensure_python_compat_shim(candidate)
    if shim_dir and shim_dir not in parts:
        parts.insert(0, shim_dir)
    return os.pathsep.join(parts)


def resolve_binary(binary_name: str, path_env: str) -> str | None:
    return shutil.which(binary_name, path=path_env)


def detect_runners(path_env: str | None = None, registry: Iterable[RunnerDefinition] = RUNNER_REGISTRY) -> list[dict]:
    resolved_path = augmented_path(path_env)
    detected: list[dict] = []

    for runner in registry:
        if not runner.binary_names:
            detected.append(
                {
                    "id": runner.id,
                    "displayName": runner.display_name,
                    "installed": True,
                    "path": None,
                    "hasAdapter": runner.has_adapter,
                    "detectionOnly": runner.detection_only,
                    "runnable": runner.has_adapter,
                    "notes": runner.notes,
                }
            )
            continue

        found_path = None
        found_binary = None
        for binary in runner.binary_names:
            candidate = resolve_binary(binary, resolved_path)
            if candidate:
                found_path = candidate
                found_binary = binary
                break

        installed = found_path is not None
        detected.append(
            {
                "id": runner.id,
                "displayName": runner.display_name,
                "installed": installed,
                "path": found_path,
                "binary": found_binary,
                "hasAdapter": runner.has_adapter,
                "detectionOnly": runner.detection_only,
                "runnable": installed and runner.has_adapter,
                "notes": runner.notes,
            }
        )

    return detected


def runner_readiness(path_env: str | None = None) -> dict:
    """For each runner that has a spawn spec, report whether its CLI is
    installed (selectable) and a hint for authenticating it.

    Most runners are "ready" when the binary is on PATH; auth failures surface
    at run time. Hermes is deeper: its home may exist with credentials that
    Hermes itself has already marked as needing re-login, so we consult
    ``hermes_status`` rather than lying that a dead token is ready.
    """
    resolved = augmented_path(path_env)
    out: dict[str, dict] = {}
    for rid, spec in selectable_runner_specs().items():
        binary = resolve_binary(spec.binary, resolved)
        out[rid] = {
            "id": rid,
            "displayName": spec.display_name,
            "installed": binary is not None,
            "binary": binary,
            "ready": binary is not None,
            "authHint": "" if binary else spec.auth_hint,
        }
    # Hermes: binary alone is not enough when auth.json says relogin_required.
    hermes = hermes_status(path_env=resolved)
    if "hermes" in out:
        out["hermes"]["ready"] = bool(hermes.get("ready"))
        if not hermes.get("ready"):
            out["hermes"]["authHint"] = str(hermes.get("guidance") or out["hermes"]["authHint"] or "")
        if hermes.get("binary"):
            out["hermes"]["binary"] = hermes["binary"]
            out["hermes"]["installed"] = True
    return out


def _hermes_home_usable(home: str) -> bool:
    p = Path(home)
    return p.is_dir() and ((p / "auth.json").exists() or (p / "config.yaml").exists())


def _hermes_auth_problem(home: str) -> str | None:
    """Return owner-facing guidance when auth.json says credentials need re-login.

    Hermes writes ``last_auth_error.relogin_required`` onto a provider after a
    failed token refresh (revoked grant, expired refresh token, etc.). Presence
    of auth.json alone used to count as ready, so Settings/Home showed Hermes
    green while every Default-profile run failed with a vague provider error.
    """
    auth_path = Path(home) / "auth.json"
    if not auth_path.is_file():
        return None
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    providers = data.get("providers")
    if not isinstance(providers, dict) or not providers:
        return None

    active = data.get("active_provider")
    names: list[str] = []
    if isinstance(active, str) and active in providers:
        names.append(active)
    names.extend(name for name in providers if name not in names)

    for name in names:
        prov = providers.get(name)
        if not isinstance(prov, dict):
            continue
        err = prov.get("last_auth_error")
        if not isinstance(err, dict) or not err.get("relogin_required"):
            continue
        msg = str(err.get("message") or "").lower()
        if "revoked" in msg or "invalid_grant" in msg:
            short = "Hermes login expired or was revoked"
        else:
            short = "Hermes login expired"
        return (
            f"{short} ({name}). Run `hermes -z` or `hermes setup` to re-authenticate, "
            "then retry - or pick a different agent in the Agents menu."
        )
    return None


def hermes_status(source_home: str | None = None, binary: str | None = None, path_env: str | None = None) -> dict:
    """Detect a usable Hermes runner without installing anything.

    Bring-your-own: reuse an existing `hermes` binary on PATH plus a Hermes home
    that has credentials/config. Returns ready + actionable guidance when not.

    If ``binary`` is provided and points to an executable file it is used
    directly; otherwise the binary is resolved via PATH.
    """
    if binary and os.path.isfile(binary) and os.access(binary, os.X_OK):
        resolved_binary = binary
    else:
        resolved = path_env if path_env is not None else augmented_path()
        resolved_binary = resolve_binary("hermes", resolved)
    home = source_home or os.path.expanduser("~/.hermes")
    home_ok = _hermes_home_usable(home)
    auth_problem = _hermes_auth_problem(home) if home_ok else None
    ready = bool(resolved_binary) and home_ok and not auth_problem
    if ready:
        guidance = ""
    elif auth_problem and resolved_binary:
        guidance = auth_problem
    elif not resolved_binary and not home_ok:
        guidance = ("Hermes not found. Install the Hermes agent CLI, run `hermes -z` "
                    "to authenticate, then restart Proxima. See docs/installation.md.")
    elif not resolved_binary:
        guidance = ("Hermes credentials found but the `hermes` binary is not on PATH. "
                    "Install/expose the Hermes CLI, then restart Proxima.")
    else:
        guidance = (f"`hermes` is installed but no usable Hermes home at {home}. "
                    "Run `hermes -z` to authenticate, or set PROXIMA_SOURCE_HERMES_HOME.")
    return {"ready": ready, "binary": resolved_binary, "home": home if home_ok else None, "guidance": guidance}
