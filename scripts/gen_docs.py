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

import ast
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

STAMP = "> **GENERATED FILE - do not edit by hand.** Regenerate with `python3 scripts/gen_docs.py`.\n"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_GENERATED_FOOTER = re.compile(r"\n---\n_Generated [^\n]+\._\n?$")


# --------------------------------------------------------------------------- API

def _collect_endpoints() -> dict[str, list[dict]]:
    """file label -> [ {methods, path, name, doc} ] parsed from decorators."""
    out: dict[str, list[dict]] = {}
    files = sorted(PKG_DIR.glob("routes/*.py")) + [PKG_DIR / "main.py"]
    for f in files:
        if f.name == "__init__.py":
            continue
        rows: list[dict] = []
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        handlers = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            key=lambda node: node.lineno,
        )
        for handler in handlers:
            routes: dict[str, list[str]] = {}
            for decorator in handler.decorator_list:
                if (
                    not isinstance(decorator, ast.Call)
                    or not isinstance(decorator.func, ast.Attribute)
                    or decorator.func.attr
                    not in (*HTTP_METHODS, "websocket", "api_route")
                    or not decorator.args
                    or not isinstance(decorator.args[0], ast.Constant)
                    or not isinstance(decorator.args[0].value, str)
                ):
                    continue
                method = decorator.func.attr
                label = "WS" if method == "websocket" else method.upper()
                routes.setdefault(decorator.args[0].value, []).append(label)
            docstring = ast.get_docstring(handler, clean=True) or ""
            doc = docstring.splitlines()[0].strip() if docstring else ""
            for path, methods in routes.items():
                rows.append(
                    {
                        "methods": methods,
                        "path": path,
                        "name": handler.name,
                        "doc": doc,
                    }
                )
        if rows:
            label = "main.py (app-level)" if f.name == "main.py" else f"routes/{f.name}"
            out[label] = rows
    return out


def _render_api(endpoints: dict[str, list[dict]]) -> str:
    total = sum(len(v) for v in endpoints.values())
    o = ["# API Reference\n", STAMP,
         f"\n{total} endpoints across {len(endpoints)} route modules. "
         "All paths are relative to the API base (e.g. `http://127.0.0.1:8765`). "
         "Auth: single-user - first run uses `POST /auth/auto` only until the owner "
         "sets a password; later sessions use `POST /auth/login`. Requests carry the "
         "HttpOnly `proxima_session` cookie or `Authorization: Bearer <token>`.\n"]
    # Quick index
    o.append("\n## Modules\n")
    for label in endpoints:
        anchor = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        o.append(f"- [`{label}`](#{anchor}) - {len(endpoints[label])} endpoints")
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
            listed.append(f"`{iname}` - {uniq}({', '.join(icols)})")
        if listed:
            o.append("\n**Indexes:** " + "; ".join(listed) + "\n")
        else:
            o.append("")
    return "\n".join(o) + "\n"


def _write_generated(path: Path, body: str, timestamp: str) -> bool:
    """Write generated content only when its semantic body changed.

    The footer records when a changed document was generated, but must not make
    the required drift check dirty an otherwise unchanged checkout on every run.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if _GENERATED_FOOTER.search(existing) and _GENERATED_FOOTER.sub("", existing) == body:
        return False
    path.write_text(f"{body}\n---\n_Generated {timestamp}._\n", encoding="utf-8")
    return True


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    endpoints = _collect_endpoints()
    api_md = _render_api(endpoints)
    api_changed = _write_generated(OUT_DIR / "api.md", api_md, ts)
    total = sum(len(v) for v in endpoints.values())
    print(
        f"{'wrote' if api_changed else 'unchanged'} docs/reference/api.md  "
        f"({total} endpoints, {len(endpoints)} modules)"
    )

    conn = _build_temp_db()
    try:
        db_md = _render_db(conn)
    finally:
        conn.close()
    db_changed = _write_generated(OUT_DIR / "database.md", db_md, ts)
    ntables = db_md.count("\n### ")
    print(
        f"{'wrote' if db_changed else 'unchanged'} docs/reference/database.md  "
        f"({ntables} tables)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
