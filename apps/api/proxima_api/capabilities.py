"""Per-runtime skill + MCP detection and activation.

Proxima is bring-your-own-agent: each runner (Claude Code, Codex, Hermes, …)
keeps its skills and MCP servers in its OWN host dir, in its OWN convention. This
module discovers what a runner actually has on THIS machine (portable - driven off
each RunnerSpec's `source_dir`, never a hardcoded absolute path) and activates a
chosen subset into a profile's seeded home so the agent loads it at run time.

Two layers:
  detect_for_runner(spec)          → what the runner has on this host (read-only)
  apply_capabilities(spec, home, …) → make the selected subset live in a profile home

Skills have multiple sources:
  1. The runner's own host dir (`source_dir` + skill subpath)
  2. OS-aware extra roots per runner (shared registries, common install paths)
  3. Owner-configured custom absolute roots (global Proxima setting)
  4. Proxima's shipped capability bundle (`bundled-skills/`, ids `bundled/<name>`)

Union + dedupe by skill id (first source wins). Results are cached so slash
palette and settings UIs can rescan deliberately rather than on every keystroke.

Everything here is defensive: a missing dir, malformed config, or unreadable file
degrades to "nothing detected"/"nothing applied" - it must never break a run.

Selection model (stored per-profile as JSON in profiles.capabilities):
  None / absent   → inherit ALL detected (best default: your skills just work)
  {"skills": [...ids], "mcp": [...names]}  → explicit override (subset / opt-out)
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

import tomlkit
import yaml

try:  # tomllib is stdlib on 3.11+; Codex and Grok configs are TOML
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

log = logging.getLogger("proxima.capabilities")

# ── path expansion (Linux / macOS / Windows) ─────────────────────────────────

_ENV_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def expand_skill_root(raw: str | Path) -> Path | None:
    """Expand `~`, `$VAR`, and Windows `%USERPROFILE%` / `%APPDATA%` style vars.

    Returns None for empty/whitespace input. Never raises.
    """
    try:
        text = str(raw or "").strip()
        if not text:
            return None
        # Windows-style %VAR% even when running under a Unix shell (portable config).
        def _win_env(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), match.group(0))

        text = _ENV_VAR_RE.sub(_win_env, text)
        text = os.path.expandvars(os.path.expanduser(text))
        # Windows path templates in the OS×runner table use backslashes; on
        # POSIX hosts normalize them so Path does not treat `\` as a character.
        if os.name != "nt" and "\\" in text:
            text = text.replace("\\", "/")
        return Path(text)
    except (TypeError, ValueError, OSError):
        return None


def os_family(system: str | None = None) -> str:
    """Normalize platform.system() to linux | macos | windows."""
    name = (system or platform.system() or "").strip().lower()
    if name == "darwin":
        return "macos"
    if name.startswith("win"):
        return "windows"
    return "linux"


# Shared skill registries (Agent-Skills style) that are not runner-owned.
SHARED_SKILL_ROOTS: dict[str, tuple[str, ...]] = {
    "linux": ("~/.agents/skills",),
    "macos": ("~/.agents/skills",),
    "windows": (
        r"%USERPROFILE%\.agents\skills",
        r"%APPDATA%\agents\skills",
    ),
}

# Extra roots per runner beyond `<source_dir>/<skill_subpath>`. Paths that
# resolve to the same directory as the primary root are skipped after expand.
RUNNER_EXTRA_SKILL_ROOTS: dict[str, dict[str, tuple[str, ...]]] = {
    "claude-code": {
        "linux": ("~/.claude/skills",),
        "macos": ("~/.claude/skills",),
        "windows": (r"%USERPROFILE%\.claude\skills",),
    },
    "codex": {
        "linux": ("~/.codex/skills", "~/_agent/skills"),
        "macos": ("~/.codex/skills", "~/_agent/skills"),
        "windows": (r"%USERPROFILE%\.codex\skills",),
    },
    "hermes": {
        "linux": ("~/.hermes/skills",),
        "macos": ("~/.hermes/skills",),
        "windows": (r"%USERPROFILE%\.hermes\skills",),
    },
    "grok": {
        "linux": ("~/.grok/skills", "~/.grok/bundled/skills"),
        "macos": ("~/.grok/skills", "~/.grok/bundled/skills"),
        "windows": (r"%USERPROFILE%\.grok\skills",),
    },
    "pi": {
        "linux": ("~/.pi/agent/skills",),
        "macos": ("~/.pi/agent/skills",),
        "windows": (r"%USERPROFILE%\.pi\agent\skills",),
    },
}


def skill_roots_for_runner(
    rid: str,
    *,
    host: Path | None = None,
    custom_roots: list[str] | None = None,
    system: str | None = None,
) -> tuple[list[Path], list[str]]:
    """Ordered skill directories to scan for a runner.

    Returns (roots, warnings). Roots are absolute/expanded, existing dirs only
    for filesystem presence checks deferred to the scanner - invalid custom
    paths produce warnings and are skipped (never raise).
    """
    family = os_family(system)
    roots: list[Path] = []
    warnings: list[str] = []
    seen: set[str] = set()

    def _add(path: Path | None, *, label: str, require_exists: bool = False) -> None:
        if path is None:
            return
        try:
            resolved = path.expanduser()
            key = str(resolved)
            try:
                key = str(resolved.resolve())
            except OSError:
                pass
            if key in seen:
                return
            if require_exists and not resolved.is_dir():
                warnings.append(f"skipped skill root ({label}): not a directory: {path}")
                return
            seen.add(key)
            roots.append(resolved)
        except OSError as exc:
            warnings.append(f"skipped skill root ({label}): {exc}")

    # 1) Primary: runner host dir + skill subpath (may not exist yet).
    rel = _skills_rel(rid)
    if host is not None and rel:
        _add(Path(host) / rel, label="runner-home")

    # 2) Runner-specific OS table.
    for template in (RUNNER_EXTRA_SKILL_ROOTS.get(rid) or {}).get(family, ()):
        _add(expand_skill_root(template), label=f"runner:{rid}")

    # 3) Shared registries (all runners).
    for template in SHARED_SKILL_ROOTS.get(family, ()):
        _add(expand_skill_root(template), label="shared")

    # 4) Owner custom absolute paths (global setting). Invalid → warn + skip.
    for raw in custom_roots or []:
        text = str(raw or "").strip()
        if not text:
            continue
        expanded = expand_skill_root(text)
        if expanded is None:
            warnings.append("skipped skill root (custom): empty path")
            continue
        if not expanded.is_absolute() and not str(text).startswith(("~", "%", "$")):
            # Prefer absolute custom roots; still try expanduser for ~-relative.
            pass
        if not expanded.is_dir():
            warnings.append(f"skipped skill root (custom): not a directory: {text}")
            continue
        _add(expanded, label="custom", require_exists=False)

    return roots, warnings


# ── host-dir resolution ──────────────────────────────────────────────────────

def _host_dir(spec: Any, source_override: str | None = None) -> Path:
    """The runner's real config dir on this host (~ expanded). `source_override`
    lets callers pass Hermes' configured source_hermes_home."""
    raw = source_override or getattr(spec, "source_dir", "") or ""
    return Path(os.path.expanduser(raw)) if raw else Path("/nonexistent")


# ── skill detection (runner-owned directories) ───────────────────────────────

def _read_skill_meta(skill_dir: Path, fallback_name: str) -> dict[str, str]:
    """Pull `name` + `description` from a skill's SKILL.md YAML frontmatter,
    without a YAML dep (frontmatter is simple key: value lines). Handles the
    `description: |` / `>` block-scalar form by taking the following indented text."""
    name, desc = fallback_name, ""
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return {"name": name, "description": desc}
    try:
        text = md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"name": name, "description": desc}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        fm = text[3:end] if end != -1 else ""
        lines = fm.splitlines()
        for i, line in enumerate(lines):
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "name" and v:
                name = v
            elif k == "description":
                if v in ("|", ">", "|-", ">-", ""):  # block scalar / empty → take indented body
                    body = []
                    for nxt in lines[i + 1:]:
                        if nxt.strip() and not nxt.startswith((" ", "\t")):
                            break
                        body.append(nxt.strip())
                    desc = " ".join(x for x in body if x)
                elif v:
                    desc = v
    return {"name": name, "description": desc[:200]}


def _detect_dir_skills(base: Path) -> list[dict[str, Any]]:
    """Skills under `base`. Two shapes coexist: flat (`<base>/<skill>/SKILL.md`) and
    grouped (`<base>/<category>/<skill>/SKILL.md`, category carries a DESCRIPTION.md).
    Grouped skills get a `category/skill` id so they stay unique and re-symlinkable."""
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return out
    for d in entries:
        try:
            if not d.is_dir() or d.name.startswith("."):  # skip hidden/internal (e.g. codex .system)
                continue
            if (d / "SKILL.md").is_file():  # flat skill
                meta = _read_skill_meta(d, d.name)
                out.append({"id": d.name, "name": meta["name"],
                            "description": meta["description"], "source": str(d)})
                continue
            # grouped: descend one level for nested skills
            for sub in sorted(d.iterdir()):
                if sub.name.startswith("."):
                    continue
                if sub.is_dir() and (sub / "SKILL.md").is_file():
                    sid = f"{d.name}/{sub.name}"
                    meta = _read_skill_meta(sub, sid)
                    out.append({"id": sid, "name": meta["name"],
                                "description": meta["description"], "source": str(sub),
                                "group": d.name})
        except OSError:
            continue
    return out


# ── bundled skills (Proxima's shipped capability bundle, T8) ─────────────────

# Bundled skills are namespaced under this group so they never collide with a
# same-named host skill, and so the existing grouped (`category/skill`) symlink
# + prune machinery handles them with no new code paths.
BUNDLED_GROUP = "bundled"


def detect_bundled_skills(bundle_dir: str | Path | None) -> list[dict[str, Any]]:
    """Skills shipped with Proxima, read from the bundle directory. Content-
    pluggable: any direct subfolder with a SKILL.md is a skill (flat only —
    the bundle keeps no category nesting); there is no skill list in code."""
    out: list[dict[str, Any]] = []
    if not bundle_dir:
        return out
    base = Path(bundle_dir)
    if not base.is_dir():
        return out
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return out
    for d in entries:
        try:
            if not d.is_dir() or d.name.startswith(".") or not (d / "SKILL.md").is_file():
                continue
            sid = f"{BUNDLED_GROUP}/{d.name}"
            meta = _read_skill_meta(d, d.name)
            out.append({"id": sid, "name": meta["name"], "description": meta["description"],
                        "source": str(d), "group": BUNDLED_GROUP, "bundled": True})
        except OSError:
            continue
    return out


# ── MCP detection (per-runner config format) ─────────────────────────────────

def _mcp_from_claude(host: Path) -> list[dict[str, Any]]:
    """Claude's global MCP servers live in ~/.claude.json (sibling of ~/.claude),
    under top-level `mcpServers`."""
    cfg = host.parent / ".claude.json"  # host is ~/.claude → ~/.claude.json
    if not cfg.is_file():
        return []
    try:
        data = json.loads(cfg.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return []
    servers = (data or {}).get("mcpServers") or {}
    return _norm_mcp(servers)


def _mcp_from_toml(host: Path) -> list[dict[str, Any]]:
    """Codex and Grok keep MCP servers in config.toml [mcp_servers.*]."""
    cfg = host / "config.toml"
    if not cfg.is_file() or tomllib is None:
        return []
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return []
    return _norm_mcp((data or {}).get("mcp_servers") or {})


def _norm_mcp(servers: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, s in servers.items():
        s = s if isinstance(s, dict) else {}
        if s.get("url") or s.get("type") in ("http", "sse"):
            kind, detail = "http", str(s.get("url") or "")
        else:
            cmd = s.get("command") or ""
            args = s.get("args") or []
            kind = "stdio"
            detail = " ".join([str(cmd), *(str(a) for a in args)]).strip()
        out.append({"name": name, "kind": kind, "detail": detail[:200]})
    return out


# ── public: detection ────────────────────────────────────────────────────────

# Where each runner keeps its skills, RELATIVE to its host config dir. Conventions
# differ (pi nests under agent/); adding a runner = one entry here, nothing hardcoded
# elsewhere (and these are ~-relative via the spec's source_dir). The same subpath is
# used as the activation target inside a profile home, so detection and seeding agree.
SKILL_SUBPATH: dict[str, str] = {
    "claude-code": "skills",
    "codex": "skills",       # symlinked in from the shared ~/_agent/skills registry
    "hermes": "skills",
    "grok": "skills",
    "pi": "agent/skills",    # pi reads Agent-Skills from ~/.pi/agent/skills
}


def _skills_rel(rid: str) -> str | None:
    return SKILL_SUBPATH.get(rid)


# Detection cache: slash palette and settings must not re-walk the filesystem on
# every keystroke. Cleared on cold start (process), manual rescan, custom-root
# edits, and optional force flags on detect endpoints.
_detect_lock = threading.Lock()
_detect_cache: dict[tuple[Any, ...], dict[str, Any]] = {}


def clear_skill_scan_cache() -> None:
    """Drop all cached skill/MCP detection results (manual Rescan + settings)."""
    with _detect_lock:
        _detect_cache.clear()


def _merge_skills(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union skill lists, first id wins (primary root preferred over shared)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for skill in group:
            sid = str(skill.get("id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            out.append(skill)
    return out


def detect_for_runner(
    spec: Any,
    source_override: str | None = None,
    bundle_dir: str | Path | None = None,
    custom_roots: list[str] | None = None,
    *,
    force_rescan: bool = False,
    system: str | None = None,
) -> dict[str, Any]:
    """What this runner has on the host right now, plus Proxima's bundled skills
    when a bundle dir is given.

    Returns `{skills: [...], mcp: [...], warnings: [...], roots: [...]}`.
    Multi-root scan: runner home, OS×runner table, shared registries, custom
    absolute roots. Dedupe by skill id. Cached unless `force_rescan`.
    """
    host = _host_dir(spec, source_override)
    rid = getattr(spec, "id", "")
    custom = [str(p).strip() for p in (custom_roots or []) if str(p).strip()]
    family = os_family(system)
    cache_key = (
        rid,
        str(host),
        str(bundle_dir or ""),
        tuple(custom),
        family,
    )
    if not force_rescan:
        with _detect_lock:
            hit = _detect_cache.get(cache_key)
            if hit is not None:
                return deepcopy(hit)

    skills: list[dict[str, Any]] = []
    mcp: list[dict[str, Any]] = []
    warnings: list[str] = []
    roots_out: list[str] = []
    try:
        rel = _skills_rel(rid)
        if rel:
            root_paths, root_warnings = skill_roots_for_runner(
                rid, host=host, custom_roots=custom, system=system
            )
            warnings.extend(root_warnings)
            for root in root_paths:
                roots_out.append(str(root))
                if root.is_dir():
                    skills = _merge_skills(skills, _detect_dir_skills(root))
                else:
                    # Primary/extra roots may be missing; that is normal, not a warn.
                    pass
            for w in root_warnings:
                log.warning("%s", w)
            skills = _merge_skills(skills, detect_bundled_skills(bundle_dir))
        if rid == "claude-code":
            mcp = _mcp_from_claude(host)
        elif rid in ("codex", "grok"):
            mcp = _mcp_from_toml(host)
        elif rid == "hermes":
            # Hermes keeps MCP inline in config.yaml.
            mcp = _mcp_from_hermes(host)
    except Exception:  # never let detection break a caller
        log.exception("capability detection failed for runner %s", rid)
    result: dict[str, Any] = {
        "skills": skills,
        "mcp": mcp,
        "warnings": warnings,
        "roots": roots_out,
    }
    with _detect_lock:
        _detect_cache[cache_key] = deepcopy(result)
    return deepcopy(result)


def _mcp_from_hermes(host: Path) -> list[dict[str, Any]]:
    cfg = host / "config.yaml"
    if not cfg.is_file():
        return []
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8", errors="ignore")) or {}
    except (OSError, ValueError):
        return []
    servers = data.get("mcpServers") or data.get("mcp_servers") or {}
    return _norm_mcp(servers) if isinstance(servers, dict) else []


# ── selection helpers ────────────────────────────────────────────────────────

def parse_selection(raw: str | None) -> dict[str, Any] | None:
    """profiles.capabilities JSON → dict, or None (= inherit all)."""
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def _selected(detected: list[dict[str, Any]], sel_ids: list[str] | None, key: str) -> list[dict[str, Any]]:
    """Detected items filtered by selection. sel_ids None → all (inherit)."""
    if sel_ids is None:
        return detected
    wanted = set(sel_ids)
    return [d for d in detected if d.get(key) in wanted]


# ── public: activation ───────────────────────────────────────────────────────

def apply_capabilities(
    spec: Any,
    home: Path,
    selection: dict[str, Any] | None,
    source_override: str | None = None,
    bundle_dir: str | Path | None = None,
    custom_roots: list[str] | None = None,
    strict: bool = False,
) -> dict[str, list[str]]:
    """Make the selected skills + MCP live in a profile's seeded home.

    Skills: symlink each selected skill dir into <home>/skills/<id> (own-the-folder:
    stays in sync with the host copy; no duplication). Bundled skills ride the same
    path under `bundled/<name>`. Symlinks not in the selection are pruned. MCP:
    rewrite the runner's config in the home to the selected subset.

    Idempotent. Returns what was applied for logging/debug. Ordinary profile
    activation is best-effort; security boundaries may set ``strict`` to fail
    closed when an explicit empty selection cannot be applied.
    """
    home = Path(home)
    rid = getattr(spec, "id", "")
    applied: dict[str, list[str]] = {"skills": [], "mcp": []}
    try:
        detected = detect_for_runner(
            spec, source_override, bundle_dir, custom_roots=custom_roots
        )
        sel = selection or {}
        skill_ids = sel.get("skills") if isinstance(sel.get("skills"), list) else None
        mcp_names = sel.get("mcp") if isinstance(sel.get("mcp"), list) else None

        rel = _skills_rel(rid)
        if rel:
            applied["skills"] = _apply_skill_symlinks(
                home / rel, _selected(detected["skills"], skill_ids, "id"))
        if rid == "claude-code":
            applied["mcp"] = _apply_claude_mcp(
                home, _selected(detected["mcp"], mcp_names, "name"), source_override, spec)
        elif rid in ("codex", "grok"):
            applied["mcp"] = _apply_toml_mcp(
                home, _selected(detected["mcp"], mcp_names, "name"), source_override, spec)
        elif rid == "hermes":
            applied["mcp"] = _apply_hermes_mcp(
                home, _selected(detected["mcp"], mcp_names, "name"), source_override, spec)
    except Exception:
        log.exception("apply_capabilities failed for runner %s", rid)
        if strict:
            raise
    return applied


def _apply_skill_symlinks(skills_home: Path, selected: list[dict[str, Any]]) -> list[str]:
    """Symlink each selected skill's source into skills_home (ids may be nested
    `category/skill`); prune stale symlinks we manage, at both depths. Real dirs the
    user/agent created are left alone."""
    applied: list[str] = []
    wanted = {s["id"]: s["source"] for s in selected}
    skills_home.mkdir(parents=True, exist_ok=True)
    # prune managed symlinks no longer selected (walk two levels: flat + grouped)
    try:
        for entry in skills_home.iterdir():
            if entry.is_symlink():
                if entry.name not in wanted:
                    _unlink_quiet(entry)
            elif entry.is_dir():  # a category group — check nested symlinks
                for sub in entry.iterdir():
                    if sub.is_symlink() and f"{entry.name}/{sub.name}" not in wanted:
                        _unlink_quiet(sub)
                _rmdir_if_empty(entry)
    except OSError:
        pass
    for sid, src in wanted.items():
        dst = skills_home / sid
        src_path = Path(src)
        if not src_path.is_dir():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_symlink():
                if os.path.realpath(dst) == os.path.realpath(src_path):
                    applied.append(sid)
                    continue
                dst.unlink()
            elif dst.exists():
                continue  # a real dir already there — don't clobber
            dst.symlink_to(src_path, target_is_directory=True)
            applied.append(sid)
        except OSError:
            try:  # cross-device or perms: copy so it still activates
                shutil.copytree(src_path, dst, dirs_exist_ok=True)
                applied.append(sid)
            except OSError:
                continue
    return applied


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _rmdir_if_empty(p: Path) -> None:
    try:
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    except OSError:
        pass


def _apply_claude_mcp(home: Path, selected: list[dict[str, Any]], source_override: str | None,
                      spec: Any) -> list[str]:
    """Rewrite <home>/.claude.json mcpServers to the selected subset. The full host
    .claude.json is copied in by seeding (all servers); this filters it to the
    profile's selection so each profile can carry a different MCP set."""
    host_cfg = _host_dir(spec, source_override).parent / ".claude.json"
    home_cfg = home / ".claude.json"
    try:
        host_data = json.loads(host_cfg.read_text(encoding="utf-8", errors="ignore")) if host_cfg.is_file() else {}
    except (OSError, json.JSONDecodeError):
        host_data = {}
    all_servers = (host_data or {}).get("mcpServers") or {}
    names = {m["name"] for m in selected}
    subset = {k: v for k, v in all_servers.items() if k in names}
    # merge into the seeded home config (preserve its other keys)
    try:
        home_data = json.loads(home_cfg.read_text(encoding="utf-8", errors="ignore")) if home_cfg.is_file() else {}
    except (OSError, json.JSONDecodeError):
        home_data = {}
    if not isinstance(home_data, dict):
        home_data = {}
    if home_data.get("mcpServers") == subset:
        return list(subset.keys())  # already in sync — don't rewrite (keeps mtime stable → no needless recycle)
    home_data["mcpServers"] = subset
    try:
        home.mkdir(parents=True, exist_ok=True)
        home_cfg.write_text(json.dumps(home_data, indent=2), encoding="utf-8")
    except OSError:
        return []
    return list(subset.keys())


def _apply_toml_mcp(home: Path, selected: list[dict[str, Any]], source_override: str | None,
                    spec: Any) -> list[str]:
    """Filter Codex/Grok [mcp_servers.*] while preserving unrelated TOML."""
    host_cfg = _host_dir(spec, source_override) / "config.toml"
    home_cfg = home / "config.toml"
    try:
        host_doc = tomlkit.parse(host_cfg.read_text(encoding="utf-8", errors="ignore"))
        home_doc = tomlkit.parse(home_cfg.read_text(encoding="utf-8", errors="ignore")) if home_cfg.is_file() else tomlkit.document()
    except (OSError, ValueError):
        return []
    source = host_doc.get("mcp_servers") or {}
    names = {m["name"] for m in selected}
    subset = tomlkit.table()
    for name, value in source.items():
        if name in names:
            subset.add(name, deepcopy(value))
    home_doc["mcp_servers"] = subset
    try:
        home.mkdir(parents=True, exist_ok=True)
        rendered = tomlkit.dumps(home_doc)
        if not home_cfg.is_file() or home_cfg.read_text(encoding="utf-8", errors="ignore") != rendered:
            home_cfg.write_text(rendered, encoding="utf-8")
    except OSError:
        return []
    return list(subset.keys())


def _apply_hermes_mcp(home: Path, selected: list[dict[str, Any]], source_override: str | None,
                      spec: Any) -> list[str]:
    """Filter Hermes' inline MCP map while preserving the rest of config.yaml."""
    host_cfg = _host_dir(spec, source_override) / "config.yaml"
    home_cfg = home / "config.yaml"
    try:
        host_data = yaml.safe_load(host_cfg.read_text(encoding="utf-8", errors="ignore")) if host_cfg.is_file() else {}
        home_data = yaml.safe_load(home_cfg.read_text(encoding="utf-8", errors="ignore")) if home_cfg.is_file() else {}
    except (OSError, ValueError, yaml.YAMLError):
        return []
    if not isinstance(host_data, dict):
        host_data = {}
    if not isinstance(home_data, dict):
        home_data = {}
    source_key = "mcpServers" if "mcpServers" in host_data else "mcp_servers"
    source = host_data.get(source_key) or {}
    names = {m["name"] for m in selected}
    subset = {name: value for name, value in source.items() if name in names} if isinstance(source, dict) else {}
    home_data.pop("mcpServers", None)
    home_data.pop("mcp_servers", None)
    home_data[source_key] = subset
    try:
        home.mkdir(parents=True, exist_ok=True)
        rendered = yaml.safe_dump(home_data, sort_keys=False, allow_unicode=True)
        if not home_cfg.is_file() or home_cfg.read_text(encoding="utf-8", errors="ignore") != rendered:
            home_cfg.write_text(rendered, encoding="utf-8")
    except OSError:
        return []
    return list(subset.keys())
