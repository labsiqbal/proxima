"""The project container's agent-managed script library (T6).

Scripts are ordinary files under ``<project>/scripts/``, written by agents as
normal job output. Each starts with a header comment block (Description /
Inputs / Outputs) — there is no separate manifest. This module owns everything
that is pure about them: path jailing, the content hash the trust model binds
approvals to, header parsing, the catalog scan the run preamble injects, and
choosing the exec argv. The two trust helpers issue SQL on the connection they
are handed and nothing else, so the whole module stays unit-testable.

Trust model (captain's decision, T6 #5): a script runs as a plan step only
after its exact bytes were approved once. The approved sha256 is persisted per
(project, script); any content change invalidates the approval and the next
run blocks until the owner re-approves. That check happens at execution time
against the bytes about to run — see ``script_runner.py``.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path, PurePosixPath

SCRIPTS_DIRNAME = "scripts"
MAX_CATALOG_SCRIPTS = 100
_HEADER_MAX_BYTES = 4096
_DESCRIPTION_MAX = 140
_COMMENT_PREFIXES = ("#", "//")


class ScriptResolutionError(ValueError):
    """A script reference does not resolve to a real file inside scripts/."""


def normalize_script_rel_path(raw: str) -> str:
    """Canonicalize an authored script reference to a scripts/-relative path.

    Accepts ``scripts/foo.sh`` and ``foo.sh`` as the same script so the two
    obvious spellings cannot become two different trust records. Rejects
    anything that could step outside the folder — the graph freezes this value,
    so it must already be safe when the plan is created, not just at run time.
    """
    text = (raw or "").strip().replace("\\", "/")
    if text.startswith(f"{SCRIPTS_DIRNAME}/"):
        text = text[len(SCRIPTS_DIRNAME) + 1 :]
    path = PurePosixPath(text)
    if (
        not text
        or not path.parts
        or path.is_absolute()
        or any(part in ("..", ".") for part in path.parts)
    ):
        raise ScriptResolutionError(
            "script command must be a relative path inside the project's scripts/ folder"
        )
    return path.as_posix()


def scripts_root(project_root: Path) -> Path:
    return Path(project_root) / SCRIPTS_DIRNAME


def resolve_script(project_root: Path, rel_path: str) -> Path:
    """Resolve one library script to a real file, jailed inside scripts/."""
    rel = normalize_script_rel_path(rel_path)
    root = scripts_root(project_root).resolve()
    target = (root / Path(*PurePosixPath(rel).parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ScriptResolutionError(
            f"script '{rel}' escapes the scripts/ folder"
        ) from exc
    if not target.is_file():
        raise ScriptResolutionError(
            f"script '{SCRIPTS_DIRNAME}/{rel}' does not exist in this project"
        )
    return target


def hash_bytes(data: bytes) -> str:
    """The sha256 the trust model binds an approval to — of the exact bytes.

    Callers that also SHOW or EXECUTE the content must hash the same in-memory
    bytes they use, not re-read the file: a second read is a TOCTOU window an
    agent editing the file concurrently could win (audit F4)."""
    return hashlib.sha256(data).hexdigest()


def content_hash(path: Path) -> str:
    """Convenience wrapper: hash a file's current bytes in one read."""
    return hash_bytes(Path(path).read_bytes())


def parse_header(text: str) -> dict[str, str]:
    """Parse a script's leading comment block into its self-description.

    Recognizes ``Description:`` / ``Inputs:`` / ``Outputs:`` lines (any case)
    inside the first run of comment lines, skipping a shebang. A header with no
    labelled description falls back to its first plain comment line, so a
    minimally-documented script still gets a catalog entry.
    """
    fields = {"description": "", "inputs": "", "outputs": ""}
    labels = {f"{key}:": key for key in fields}
    fallback = ""
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped.startswith("#!"):
            continue
        prefix = next((p for p in _COMMENT_PREFIXES if stripped.startswith(p)), None)
        if prefix is None:
            if not stripped:
                continue  # blank lines inside the header block are fine
            break  # first code line ends the header
        body = stripped[len(prefix) :].strip()
        lowered = body.lower()
        label = next((l for l in labels if lowered.startswith(l)), None)
        if label is not None:
            fields[labels[label]] = body[len(label) :].strip()
        elif body and not fallback:
            fallback = body
    if not fields["description"]:
        fields["description"] = fallback
    fields["description"] = fields["description"][:_DESCRIPTION_MAX]
    return fields


def scan_catalog(project_root: Path) -> list[dict[str, str]]:
    """The library catalog: every script's rel_path + one-line description.

    Best-effort and cheap by design — it runs on every preamble build. Sorted
    for a stable prompt; capped so a runaway folder cannot flood the context.
    """
    root = scripts_root(project_root)
    if not root.is_dir():
        return []
    entries: list[dict[str, str]] = []
    try:
        files = sorted(p for p in root.rglob("*") if p.is_file())
    except OSError:
        return []
    for path in files:
        if len(entries) >= MAX_CATALOG_SCRIPTS:
            break
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in PurePosixPath(rel).parts):
            continue
        try:
            head = path.read_bytes()[:_HEADER_MAX_BYTES].decode("utf-8", errors="ignore")
        except OSError:
            continue
        entries.append({"rel_path": rel, "description": parse_header(head)["description"]})
    return entries


def catalog_preamble_block(catalog: list[dict[str, str]]) -> str:
    """The script-library block injected into the run preamble (T6 #3).

    Mirrors the wiki catalog's move: inline what exists so the agent SEES the
    library without a tool call — reuse awareness is the make-or-break of the
    deterministic-step payoff.
    """
    lines = ["## Script library (scripts/)"]
    if catalog:
        lines.append(
            "This project keeps reusable deterministic scripts in scripts/. Current catalog:"
        )
        lines += [
            f"- {SCRIPTS_DIRNAME}/{entry['rel_path']}"
            + (f" — {entry['description']}" if entry["description"] else "")
            for entry in catalog
        ]
        lines.append(
            "Prefer REUSING or extending one of these scripts over writing a new one."
        )
    else:
        lines.append(
            "This project keeps reusable deterministic scripts in scripts/ (none yet)."
        )
    lines += [
        "When a piece of work is mechanical and repeatable (fetch, convert, check, "
        "publish — anything needing no judgment), save it as a script in scripts/ so "
        "future plans can run it deterministically, without an agent. Start every "
        "script with a header comment block: '# Description: …', '# Inputs: …', "
        "'# Outputs: …' (one line each). As a plan step a script receives CLI args "
        "plus one JSON object on stdin ({\"job_input\": …, \"upstream\": […]}), must "
        "print its result to stdout, and exit 0 on success.",
    ]
    return "\n".join(lines)


def exec_argv(path: Path) -> list[str]:
    """How to execute one script: an exec ARRAY, never a shell string (T6 #6).

    An executable file runs directly (its shebang decides the interpreter).
    Otherwise the extension picks a known interpreter, because agent-written
    scripts routinely land without the executable bit. Anything else is a
    loud error rather than a guess.
    """
    target = Path(path)
    if os.access(target, os.X_OK):
        return [str(target)]
    suffix = target.suffix.lower()
    if suffix in (".sh", ".bash"):
        return ["bash", str(target)]
    if suffix == ".py":
        return [sys.executable, str(target)]
    if suffix in (".js", ".mjs"):
        return ["node", str(target)]
    raise ScriptResolutionError(
        f"script '{target.name}' is not executable and has no known interpreter "
        "(make it executable, or use a .sh/.py/.js extension)"
    )


def trusted_hash(conn: sqlite3.Connection, project_id: int, rel_path: str) -> str | None:
    """The approved content hash for one script, or None if never approved."""
    row = conn.execute(
        "SELECT content_hash FROM script_trust WHERE project_id = ? AND rel_path = ?",
        (project_id, normalize_script_rel_path(rel_path)),
    ).fetchone()
    return str(row["content_hash"]) if row else None


def record_trust(
    conn: sqlite3.Connection,
    project_id: int,
    rel_path: str,
    digest: str,
    user_id: int | None,
) -> None:
    """Bind the owner's one-time approval to the script's current bytes.

    One row per (project, script): re-approving after an edit replaces the
    hash, which is exactly the re-approval-on-change contract.
    """
    conn.execute(
        """
        INSERT INTO script_trust(project_id, rel_path, content_hash, approved_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id, rel_path) DO UPDATE SET
          content_hash = excluded.content_hash,
          approved_by = excluded.approved_by,
          approved_at = CURRENT_TIMESTAMP,
          updated_at = CURRENT_TIMESTAMP
        """,
        (project_id, normalize_script_rel_path(rel_path), digest, user_id),
    )
