from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerSpec:
    id: str
    spawn_argv: list[str]
    home_env: str          # env var that points the agent at its per-profile home
    binary: str            # the underlying CLI that must be installed/authenticated
    display_name: str
    auth_hint: str = ""
    binary_names: tuple[str, ...] = ()
    has_adapter: bool = True
    detection_only: bool = False
    notes: str = ""
    # Wire protocol the run layer speaks to this runner. "acp" (default) drives a
    # persistent ACP JSON-RPC subprocess via acp.AcpProcess. "codex-app-server"
    # drives the system Codex CLI's `codex app-server` via
    # codex_appserver.CodexAppServerProcess (same call surface, different wire).
    protocol: str = "acp"
    # Host dir the agent's login lives in (~ is expanded at use). Proxima copies the
    # listed files into each new profile home so the agent is authenticated out of
    # the box, and re-copies refresh_files before each run to follow token rotation.
    source_dir: str = ""
    seed_files: tuple[str, ...] = ()
    refresh_files: tuple[str, ...] = ()


RUNNER_SPECS: dict[str, RunnerSpec] = {
    "hermes": RunnerSpec(
        id="hermes",
        spawn_argv=["hermes", "acp", "--accept-hooks"],
        home_env="HERMES_HOME",
        binary="hermes",
        display_name="Hermes",
        auth_hint="Install the Hermes CLI and authenticate (e.g. `hermes -z`).",
        notes="Hermes Agent CLI / gateway runner",
        source_dir="~/.hermes",
        seed_files=(".env", "auth.json", "config.yaml"),
        refresh_files=("auth.json", "config.yaml"),
    ),
    "claude-code": RunnerSpec(
        id="claude-code",
        spawn_argv=["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
        home_env="CLAUDE_CONFIG_DIR",
        binary="claude",
        display_name="Claude Code",
        auth_hint="Install Claude Code and run `claude /login` (or set ANTHROPIC_API_KEY).",
        notes="Anthropic Claude Code CLI",
        source_dir="~/.claude",
        seed_files=(".credentials.json", ".claude.json"),
        refresh_files=(".credentials.json",),
    ),
    "codex": RunnerSpec(
        id="codex",
        # Drive the owner's own Codex CLI (`codex app-server`, stdio JSON-RPC),
        # NOT the Zed `@zed-industries/codex-acp` adapter. That adapter statically
        # bundles its own Codex core, which lags releases and gets rejected by the
        # ChatGPT backend for newer models ("requires a newer version of Codex")
        # even when the system `codex` runs them fine. Driving the system CLI
        # tracks the owner's up-to-date Codex, so the runner never falls behind a
        # model release. See codex_appserver.CodexAppServerProcess.
        spawn_argv=["codex", "app-server"],
        protocol="codex-app-server",
        home_env="CODEX_HOME",
        binary="codex",
        display_name="Codex",
        auth_hint="Install the Codex CLI and run `codex login` (or set OPENAI_API_KEY).",
        notes="OpenAI Codex CLI (native app-server)",
        source_dir="~/.codex",
        seed_files=("auth.json", "config.toml"),
        refresh_files=("auth.json",),
    ),
    "pi": RunnerSpec(
        id="pi",
        spawn_argv=["npx", "-y", "pi-acp"],
        # pi-acp bridges ACP ↔ `pi --mode rpc`. pi keeps its config/auth in the global
        # ~/.pi (no per-profile home env), so profiles share that provider setup.
        # Bring-your-own: configure providers once via `pi config`.
        home_env="",
        binary="pi",
        display_name="Pi",
        auth_hint="Install pi (pi.dev) and configure a provider (run `pi config`, or set ANTHROPIC_API_KEY / OPENAI_API_KEY).",
        notes="Pi coding agent (Earendil) via the pi-acp ACP adapter",
        source_dir="~/.pi",
        seed_files=(),
        refresh_files=(),
    ),
}

# The ONE place a runner name is written as a literal. Every other module resolves
# the default through default_runner(); nothing else hardcodes a vendor.
FALLBACK_RUNNER = "claude-code"


def default_runner() -> str:
    """Resolve the default runner without baking a vendor into app logic.

    Order: explicit env override (PROXIMA_DEFAULT_RUNNER) → the first installed-and-
    ready runner detected → FALLBACK_RUNNER. Runner-agnostic by design — bring your
    own agent and whichever one is actually ready wins.
    """
    env = os.environ.get("PROXIMA_DEFAULT_RUNNER")
    if env and env in RUNNER_SPECS:
        return env
    try:  # lazy import: runners imports this module
        from .runners import runner_readiness
        ready = runner_readiness()
        for rid in selectable_runner_ids():
            if ready.get(rid, {}).get("ready"):
                return rid
    except Exception:
        pass
    return FALLBACK_RUNNER


def runner_spec(runner_id: str | None) -> RunnerSpec:
    spec = RUNNER_SPECS.get(runner_id or default_runner())
    if spec and runner_is_selectable(spec.id):
        return spec
    return RUNNER_SPECS[FALLBACK_RUNNER]


def runner_binary_names(spec: RunnerSpec) -> tuple[str, ...]:
    return spec.binary_names or ((spec.binary,) if spec.binary else ())


def runner_is_selectable(runner_id: str | None) -> bool:
    spec = RUNNER_SPECS.get(runner_id or "")
    return bool(spec and spec.has_adapter and not spec.detection_only and spec.spawn_argv)


def selectable_runner_ids() -> tuple[str, ...]:
    return tuple(rid for rid in RUNNER_SPECS if runner_is_selectable(rid))


def selectable_runner_specs() -> dict[str, RunnerSpec]:
    return {rid: spec for rid, spec in RUNNER_SPECS.items() if runner_is_selectable(rid)}
