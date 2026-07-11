"""Per-runtime skill + MCP detection and activation.

Proxima is bring-your-own-agent: each runner (Claude Code, Codex, Hermes, …)
keeps its skills and MCP servers in its OWN host dir, in its OWN convention. This
module discovers what a runner actually has on THIS machine (portable — driven off
each RunnerSpec's `source_dir`, never a hardcoded absolute path) and activates a
chosen subset into a profile's seeded home so the agent loads it at run time.

Two layers:
  detect_for_runner(spec)          → what the runner has on this host (read-only)
  apply_capabilities(spec, home, …) → make the selected subset live in a profile home

Everything here is defensive: a missing dir, malformed config, or unreadable file
degrades to "nothing detected"/"nothing applied" — it must never break a run.

Selection model (stored per-profile as JSON in profiles.capabilities):
  None / absent   → inherit ALL detected (best default: your skills just work)
  {"skills": [...ids], "mcp": [...names]}  → explicit override (subset / opt-out)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

try:  # tomllib is stdlib on 3.11+; codex config is TOML
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

log = logging.getLogger("proxima.capabilities")


# ── host-dir resolution ──────────────────────────────────────────────────────

def _host_dir(spec: Any, source_override: str | None = None) -> Path:
    """The runner's real config dir on this host (~ expanded). `source_override`
    lets callers pass Hermes' configured source_hermes_home."""
    raw = source_override or getattr(spec, "source_dir", "") or ""
    return Path(os.path.expanduser(raw)) if raw else Path("/nonexistent")


# ── skill detection (dir-of-skills convention: claude, hermes) ───────────────

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


def _mcp_from_codex(host: Path) -> list[dict[str, Any]]:
    """Codex MCP lives in ~/.codex/config.toml under [mcp_servers.<name>]."""
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
    "pi": "agent/skills",    # pi reads Agent-Skills from ~/.pi/agent/skills
}


def _skills_rel(rid: str) -> str | None:
    return SKILL_SUBPATH.get(rid)


def detect_for_runner(spec: Any, source_override: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """What this runner has on the host right now. `{skills: [...], mcp: [...]}`."""
    host = _host_dir(spec, source_override)
    rid = getattr(spec, "id", "")
    skills: list[dict[str, Any]] = []
    mcp: list[dict[str, Any]] = []
    try:
        rel = _skills_rel(rid)
        if rel:
            skills = _detect_dir_skills(host / rel)
        if rid == "claude-code":
            mcp = _mcp_from_claude(host)
        elif rid == "codex":
            mcp = _mcp_from_codex(host)
        elif rid == "hermes":
            # Hermes keeps MCP inline in config.yaml; parse best-effort if PyYAML
            # is available, else skip (skills still detected).
            mcp = _mcp_from_hermes(host)
    except Exception:  # never let detection break a caller
        log.exception("capability detection failed for runner %s", rid)
    return {"skills": skills, "mcp": mcp}


def _mcp_from_hermes(host: Path) -> list[dict[str, Any]]:
    cfg = host / "config.yaml"
    if not cfg.is_file():
        return []
    try:
        import yaml  # optional
    except ModuleNotFoundError:
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

def apply_capabilities(spec: Any, home: Path, selection: dict[str, Any] | None,
                       source_override: str | None = None) -> dict[str, list[str]]:
    """Make the selected skills + MCP live in a profile's seeded home.

    Skills: symlink each selected skill dir into <home>/skills/<id> (own-the-folder:
    stays in sync with the host copy; no duplication). Symlinks not in the selection
    are pruned. MCP: rewrite the runner's config in the home to the selected subset.

    Idempotent. Returns what was applied for logging/debug. Never raises.
    """
    home = Path(home)
    rid = getattr(spec, "id", "")
    applied = {"skills": [], "mcp": []}
    try:
        detected = detect_for_runner(spec, source_override)
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
        # codex/hermes MCP activation: their config is copied wholesale by seeding
        # today; filtering is a follow-up (documented). Detection already surfaces them.
    except Exception:
        log.exception("apply_capabilities failed for runner %s", rid)
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
