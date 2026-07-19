#!/usr/bin/env python3
"""Regenerate the code-derived reference docs so they always match the source.

Two docs are produced, both marked GENERATED so no one hand-edits them:

- ``docs/reference/api.md``      — every HTTP/WebSocket endpoint, parsed from the
  route decorators in ``apps/api/proxima_api`` (``@app.get(...)`` etc.).
- ``docs/reference/database.md`` — every SQLite table/column/index, introspected
  from a throwaway database built with the app's own ``init_db`` + migrations, so
  the schema is exactly what a fresh install gets.

Run it after any change to routes or the DB schema:

    python3 scripts/gen_docs.py            # from the repo root

It has no third-party dependencies (stdlib + the app package only). The database
step imports the app package, so it needs the api deps importable — run it with
the api venv if the bare interpreter can't import ``proxima_api``:

    apps/api/.venv/bin/python scripts/gen_docs.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API_PKG = REPO / "apps" / "api"
PKG_DIR = API_PKG / "proxima_api"
OUT_DIR = REPO / "docs" / "reference"

STAMP = "> **GENERATED FILE — do not edit by hand.** Regenerate with `python3 scripts/gen_docs.py`.\n"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


# --------------------------------------------------------------------------- API

# Matches e.g.  @app.get("/api/sessions")   or   @app.websocket("/api/ws/...")
# (any receiver name — routes use `app`, but stay tolerant of aliases.)
_DECORATOR = re.compile(
    r"""^\s*@\w+\.(?P<method>get|post|put|patch|delete|head|options|websocket|api_route)\(\s*
        (?P<q>['"])(?P<path>[^'"]+)(?P=q)""",
    re.VERBOSE,
)
_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>\w+)\s*\(")


def _first_doc_line(lines: list[str], def_idx: int) -> str:
    """Return the first line of the handler's docstring, if any (else '')."""
    for j in range(def_idx, min(def_idx + 6, len(lines))):
        m = re.search(r'"""(.*)', lines[j])
        if m:
            text = m.group(1).strip()
            if text.endswith('"""'):
                text = text[:-3].strip()
            return text
    return ""


def _collect_endpoints() -> dict[str, list[dict]]:
    """file label -> [ {methods, path, name, doc} ] parsed from decorators."""
    out: dict[str, list[dict]] = {}
    files = sorted(PKG_DIR.glob("routes/*.py")) + [PKG_DIR / "main.py"]
    for f in files:
        if f.name == "__init__.py":
            continue
        lines = f.read_text().splitlines()
        rows: list[dict] = []
        i = 0
        while i < len(lines):
            m = _DECORATOR.match(lines[i])
            if not m:
                i += 1
                continue
            # Stack consecutive decorators (a handler can carry several methods).
            methods = []
            path = m.group("path")
            while i < len(lines):
                mm = _DECORATOR.match(lines[i])
                if mm and mm.group("path") == path:
                    methods.append("WS" if mm.group("method") == "websocket" else mm.group("method").upper())
                    i += 1
                    continue
                break
            # Find the handler def that follows the decorator stack.
            name, doc = "", ""
            for j in range(i, min(i + 4, len(lines))):
                dm = _DEF.match(lines[j])
                if dm:
                    name = dm.group("name")
                    doc = _first_doc_line(lines, j)
                    break
            rows.append({"methods": methods, "path": path, "name": name, "doc": doc})
        if rows:
            label = "main.py (app-level)" if f.name == "main.py" else f"routes/{f.name}"
            out[label] = rows
    return out


def _render_api(endpoints: dict[str, list[dict]]) -> str:
    total = sum(len(v) for v in endpoints.values())
    o = ["# API Reference\n", STAMP,
         f"\n{total} endpoints across {len(endpoints)} route modules. "
         "All paths are relative to the API base (e.g. `http://127.0.0.1:8765`). "
         "Auth: single-user — first run uses `POST /auth/auto` only until the owner "
         "sets a password; later sessions use `POST /auth/login`. Requests carry the "
         "HttpOnly `proxima_session` cookie or `Authorization: Bearer <token>`.\n"]
    # Quick index
    o.append("\n## Modules\n")
    for label in endpoints:
        anchor = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        o.append(f"- [`{label}`](#{anchor}) — {len(endpoints[label])} endpoints")
    o.append("")
    for label, rows in endpoints.items():
        o.append(f"\n## {label}\n")
        o.append("| Method | Path | Handler | Description |")
        o.append("| --- | --- | --- | --- |")
        for r in sorted(rows, key=lambda x: (x["path"], x["methods"])):
            methods = "<br>".join(r["methods"])
            doc = (r["doc"] or "").replace("|", "\\|")
            o.append(f"| {methods} | `{r['path']}` | `{r['name']}` | {doc} |")
        o.append("")
    return "\n".join(o) + "\n"


# ----------------------------------------------------------------------- DATABASE

def _build_temp_db() -> sqlite3.Connection:
    """Build a fresh DB exactly like a real install (SCHEMA + migrate + versioned)."""
    if str(API_PKG) not in sys.path:
        sys.path.insert(0, str(API_PKG))
    from proxima_api.db import connect, init_db  # noqa: E402
    from proxima_api.migrations import run_migrations  # noqa: E402

    tmp = Path(tempfile.mkdtemp(prefix="proxima-docgen-")) / "schema.db"
    conn = connect(tmp)
    init_db(conn, [], None, None)
    run_migrations(conn, str(tmp))
    return conn


def _render_db(conn: sqlite3.Connection) -> str:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    schema_version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    o = ["# Database Schema\n", STAMP,
         f"\nSQLite (WAL mode). {len(tables)} tables. Applied migration version: "
         f"**{schema_version}**. This is the exact shape a fresh install gets from "
         "`init_db` + versioned migrations. Per-install data lives at "
         "`~/.local/share/proxima/proxima.db` (outside the repo).\n"]
    o.append("\n## Tables\n")
    o.append(", ".join(f"[`{t}`](#{t})" for t in tables) + "\n")

    for t in tables:
        o.append(f"\n### {t}\n")
        cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
        # foreign_key_list row: (id, seq, table, from, to, on_update, on_delete, match)
        fk_full = {f[3]: (f[2], f[4], f[6]) for f in conn.execute(f"PRAGMA foreign_key_list({t})").fetchall()}
        o.append("| Column | Type | Null | Default | Key / FK |")
        o.append("| --- | --- | --- | --- | --- |")
        for c in cols:
            _cid, name, ctype, notnull, dflt, pk = c
            null = "NO" if notnull else "yes"
            default = f"`{dflt}`" if dflt is not None else ""
            keys = []
            if pk:
                keys.append("PK")
            if name in fk_full:
                ref_t, ref_c, on_del = fk_full[name]
                fk = f"→ `{ref_t}.{ref_c}`"
                if on_del and on_del != "NO ACTION":
                    fk += f" (ON DELETE {on_del})"
                keys.append(fk)
            o.append(f"| `{name}` | {ctype or ''} | {null} | {default} | {' '.join(keys)} |")
        # Indexes for this table
        idx = conn.execute(f"PRAGMA index_list({t})").fetchall()
        listed = []
        for row in idx:
            iname = row[1]
            if iname.startswith("sqlite_autoindex"):
                continue
            icols = [r[2] for r in conn.execute(f"PRAGMA index_info({iname})").fetchall()]
            uniq = "UNIQUE " if row[2] else ""
            listed.append(f"`{iname}` — {uniq}({', '.join(icols)})")
        if listed:
            o.append("\n**Indexes:** " + "; ".join(listed) + "\n")
        else:
            o.append("")
    return "\n".join(o) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    endpoints = _collect_endpoints()
    api_md = _render_api(endpoints) + f"\n---\n_Generated {ts}._\n"
    (OUT_DIR / "api.md").write_text(api_md)
    total = sum(len(v) for v in endpoints.values())
    print(f"wrote docs/reference/api.md  ({total} endpoints, {len(endpoints)} modules)")

    conn = _build_temp_db()
    try:
        db_md = _render_db(conn) + f"\n---\n_Generated {ts}._\n"
    finally:
        conn.close()
    (OUT_DIR / "database.md").write_text(db_md)
    ntables = db_md.count("\n### ")
    print(f"wrote docs/reference/database.md  ({ntables} tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
