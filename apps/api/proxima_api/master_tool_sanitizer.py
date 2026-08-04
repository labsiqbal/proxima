"""Shape-aware sanitization of every Master tool result (#165).

Why this module exists
----------------------
The Master broker must never put an absolute host path, the owner's home
directory, or a configuration location into a model's context
(``docs/prompt-injection-hardening.md``). Until follow-the-folder (#130-#138) a
blunt rule was enough: scan the serialized tool result for anything path-shaped
and, on a hit, refuse the **whole** response.

Follow-the-folder made a project a real folder. Container identity text is read
from the project's own ``README``/``AGENTS.md``, code-graph node labels are
Area-relative file paths, and Task titles are written by agents about files. So
the blunt rule started firing on the owner's ordinary Fleet: ``list_containers``
and ``query_context`` returned nothing but "contains an unsafe filesystem
reference", and Master could not even name the owner's projects.

The principle did not change - the mechanism did. This module replaces
block-everything with **sanitize-then-pass**:

1. **Allowlist-shaped, per payload.** Each tool result has a declared shape
   (:data:`_TOOL_RESULTS`). Only declared fields survive; every undeclared field
   is dropped before anything is serialized for the model. This is deliberately
   *not* a regex blocklist over serialized JSON: a path planted in a field the
   shape does not declare never has to be detected, because it is never carried.
2. **Redaction inside declared free text.** Product prose (display names,
   identity summaries, Task titles, blocked reasons, graph labels) is rewritten
   by :func:`redact_host_paths`, which replaces absolute paths, home/config
   locations, local-file URIs, UNC and drive paths, traversal, secret file
   names, and credential material with a placeholder. Scope-relative references
   (``docs/architecture.md``) survive on purpose - they are what makes Master's
   answers citable.
3. **Verify, then refuse loudly.** Every sanitized scalar is re-checked with
   :func:`host_path_leak`. If anything still reads as a host path, that payload
   is refused with :class:`UnsanitizablePayload`, naming the field and the next
   step (#133) - never silently dropped, and never passed through.

The boundary is therefore at least as strong as before: an absolute host path
could reach the model only by surviving (1), (2) and (3) at once. What changed
is that a *scope-relative* reference inside a declared product field is now
carried instead of destroying the whole response.

Two detectors, on purpose
-------------------------
:func:`strict_path_or_secret` is the **input** policy the broker applies to
model-supplied arguments - unchanged and deliberately paranoid (any
path-shaped token at all). :func:`host_path_leak` is the **output** policy: it
fires on host paths and secrets, not on scope-relative product text.
"""
from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import unquote

log = logging.getLogger("proxima.master.sanitizer")

#: What a redacted host path and a redacted secret look like to the model.
PATH_PLACEHOLDER = "[host path removed]"
#: Deliberately free of the words the secret-file detector matches, so a
#: placeholder can never be mistaken for the thing it replaced.
SECRET_PLACEHOLDER = "[redacted]"

#: Bounds so one malformed record cannot inflate a tool result.
MAX_TEXT_CHARS = 4_000
MAX_ARRAY_ITEMS = 200
MAX_MAP_KEYS = 32

_SAFE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

# --- Shared path/secret patterns -------------------------------------------
# Remote schemes may appear in product text. Local-file schemes must not.
# The scheme needs two or more characters on purpose: a one-letter "scheme" is
# a Windows drive (``C://Users/...``), and masking it as a URL would hide it
# from both the redactor and the verifier.
_SAFE_REMOTE_URI = re.compile(
    r"""(?i)\b(?!(?:file|vscode):)[a-z][a-z0-9+.-]+://[^\s"'<>]+"""
)
_LOCAL_FILE_URI = re.compile(
    r"""(?i)\b(?:file:|vscode://file)[^\s"'<>]*"""
)
_WINDOWS_DRIVE_PATH = re.compile(
    r"""(?i)(?<![A-Za-z0-9])[A-Za-z]:[/\\]+[^\s"'<>]+"""
)
#: Input policy: any absolute-looking token, including a bare ``~``.
_ABSOLUTE_PATH_TEXT = re.compile(
    r"""(?ix)(?<![A-Za-z0-9])(?:"""
    r"""file:(?:/{1,3}|\\\\)[^\s"'<>]*|"""
    r"""vscode://file[^\s"'<>]*|"""
    r"""[A-Za-z]:[/\\][^\s"'<>]+|"""
    r"""\\\\[^\s"'<>]+|"""
    r"""~(?:[/\\][^\s"'<>]*)?|"""
    r"""(?:\.\.?[/\\])[^\s"'<>]+|"""
    r"""/(?!/)[^\s"'<>]*"""
    r""")"""
)
#: Output policy: the same host-path forms, minus the two shapes that carry no
#: host information and only mangle prose - a bare ``~`` and a lone ``/``.
_HOST_PATH_TEXT = re.compile(
    r"""(?ix)(?<![A-Za-z0-9])(?:"""
    r"""file:(?:/{1,3}|\\\\)[^\s"'<>]*|"""
    r"""vscode://file[^\s"'<>]*|"""
    r"""[A-Za-z]:[/\\][^\s"'<>]+|"""
    r"""\\\\[^\s"'<>]+|"""
    r"""~[/\\][^\s"'<>]*|"""
    r"""(?:\.\.?[/\\])[^\s"'<>]+|"""
    r"""/(?!/)[^\s"'<>]+"""
    r""")"""
)
#: Percent-encoded separators: a decoded ``%2Fhome%2Fowner`` is still a path.
_ENCODED_PATH = re.compile(r"""(?i)(?:%2f|%5c|%7e%2f)[A-Za-z0-9%._~+-]*""")
_RELATIVE_CANDIDATE = re.compile(
    r"""(?u)(?<![A-Za-z0-9._\-/])"""
    r"""(?:[\w.-]+(?:/[\w.-]+)+|\./[\w./-]+|\.\./[\w./-]+)"""
    r"""(?![A-Za-z0-9._\-/])"""
)
#: Input policy: ordinary project files count as path-shaped for arguments.
_FILE_BASENAME = re.compile(
    r"""(?ix)(?<![A-Za-z0-9._-])(?:"""
    r"""(?:README|LICENSE|CHANGELOG|package|pyproject|Cargo|go|tsconfig|vite\.config)"""
    r"""\.[A-Za-z0-9._-]+"""
    r"""|"""
    r"""(?:\.env(?:\.[A-Za-z0-9._-]+)?|id_rsa|id_ed25519|secrets?|credentials?)"""
    r"""(?:\.[A-Za-z0-9._-]+)?"""
    r""")(?![A-Za-z0-9._-])"""
)
#: Output policy: only the credential-bearing basenames are removed, so a
#: summary may still say "identity from README.md".
_SECRET_BASENAME = re.compile(
    r"""(?ix)(?<![A-Za-z0-9._-])"""
    r"""(?:\.env(?:\.[A-Za-z0-9._-]+)?|id_rsa|id_ed25519|secrets?|credentials?)"""
    r"""(?:\.[A-Za-z0-9._-]+)?"""
    r"""(?![A-Za-z0-9._-])"""
)
_FILE_EXT = re.compile(
    r"""(?i)\.(?:md|mdx|txt|rst|py|ts|tsx|js|jsx|json|toml|ya?ml|env|pem|key|"""
    r"""html?|css|go|rs|java|c|cc|cpp|h|hpp|sh|bash|zsh|sql|graphql)$"""
)
_KNOWN_PATH_ROOTS = frozenset(
    {
        "wiki",
        "ops",
        "artifacts",
        "reports",
        "graphify-out",
        "src",
        "apps",
        "docs",
        "scripts",
        "tasks",
        "uploads",
        "exports",
        "node_modules",
        "home",
        "users",
        "etc",
        "var",
        "tmp",
    }
)
# Ordinary English slash compounds - never treat as filesystem paths.
_PROSE_SLASH_PHRASES = frozenset(
    {
        "and/or",
        "ci/cd",
        "read/write",
        "i/o",
        "tcp/ip",
        "frontend/backend",
        "client/server",
        "input/output",
        "pass/fail",
        "on/off",
        "yes/no",
        "source/target",
        "test/production",
        "app/server",
        "black/white",
        "in/out",
        "up/down",
        "he/she",
        "s/he",
    }
)
_SECRET_TEXT = re.compile(
    r"""(?i)(?:\bbearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----|"""
    r"""\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,})"""
)

HOST_PATH_REASON = "a host filesystem path"
SECRET_REASON = "credential-like material"
_SCOPE_REASON = "a path outside the Area scope"

#: One next step for every refusal this module can raise. Kept here rather than
#: imported from ``refusals`` on purpose: refusals a runner sees stay terse (see
#: docs/security-boundaries.md), but they must still say what to do next.
_NEXT_STEP = (
    "Open that record in Proxima and remove the host path from its text, "
    "then ask again."
)


class UnsanitizablePayload(Exception):
    """One tool payload could not be made safe, so it was refused.

    Carries *what* was refused (tool + field), *why* (a category, never the
    offending text - repeating it would defeat the point), and the next step.
    """

    code = "unsanitizable_tool_result"

    def __init__(self, tool: str, field: str, reason: str):
        self.tool = tool
        self.field = field
        self.reason = reason
        self.next_step = _NEXT_STEP
        super().__init__(
            f"{tool} refused its own {field}: it still contains {reason} "
            f"after redaction. {self.next_step}"
        )


def _mask_safe_remote_uris(value: str) -> str:
    """Blank out ordinary remote URIs so path scans do not match their slashes.

    Local-file URI schemes are left in place so path detection still catches
    them. Masking preserves length, so offsets stay valid for redaction.
    """

    def _blank(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    return _SAFE_REMOTE_URI.sub(_blank, value)


def _relative_path_like(candidate: str) -> bool:
    """True when a slash-separated token looks like a filesystem path."""
    lowered = candidate.replace("\\", "/").strip().lower()
    if not lowered or lowered in _PROSE_SLASH_PHRASES:
        return False
    if lowered.startswith("./") or lowered.startswith("../") or ".." in lowered.split("/"):
        return True
    parts = [part for part in lowered.split("/") if part]
    if len(parts) < 2:
        return False
    if parts[0] in _KNOWN_PATH_ROOTS:
        return True
    if any(_FILE_EXT.search(part) for part in parts):
        return True
    # Unicode multi-component paths without extension are still path-like.
    if any(ord(ch) > 127 for ch in candidate):
        return True
    return False


def strict_path_or_secret(value: str) -> str | None:
    """Input policy: reject any path-shaped or credential-like argument text.

    Unchanged from the original broker rule. Model-supplied arguments carry no
    legitimate filesystem reference at all, so this stays maximally strict.
    """
    if _LOCAL_FILE_URI.search(value) or _WINDOWS_DRIVE_PATH.search(value):
        return "a filesystem path"
    masked = _mask_safe_remote_uris(value)
    if _ABSOLUTE_PATH_TEXT.search(masked):
        return "a filesystem path"
    if _FILE_BASENAME.search(masked):
        return "a filesystem path"
    for match in _RELATIVE_CANDIDATE.finditer(masked):
        if _relative_path_like(match.group(0)):
            return "a filesystem path"
    if _SECRET_TEXT.search(value):
        return "credential-like material"
    return None


def host_path_leak(value: str) -> str | None:
    """Output policy: does this model-bound text still carry a host path?

    Fires on absolute paths, home and config locations, local-file URIs, UNC
    and drive paths, traversal, percent-encoded separators, secret file names,
    and credential material. It deliberately does *not* fire on scope-relative
    product text - that is what the sanitizer is allowed to pass through.
    """
    # The raw value is scanned too: masking can never be allowed to hide a
    # local-file URI or a drive path behind a URL-shaped prefix.
    if _LOCAL_FILE_URI.search(value) or _WINDOWS_DRIVE_PATH.search(value):
        return HOST_PATH_REASON
    masked = _mask_safe_remote_uris(value)
    decoded = unquote(masked)
    for candidate in (masked, decoded) if decoded != masked else (masked,):
        if _LOCAL_FILE_URI.search(candidate):
            return HOST_PATH_REASON
        if _WINDOWS_DRIVE_PATH.search(candidate):
            return HOST_PATH_REASON
        if _HOST_PATH_TEXT.search(candidate):
            return HOST_PATH_REASON
        if _SECRET_BASENAME.search(candidate):
            return SECRET_REASON
    if _ENCODED_PATH.search(masked):
        return HOST_PATH_REASON
    if _SECRET_TEXT.search(value):
        return SECRET_REASON
    return None


def redact_host_paths(value: str) -> str:
    """Replace every host-path and credential span with a placeholder.

    Operates on the length-preserving masked copy so remote URLs keep their
    slashes without being mistaken for paths, then applies the replacements to
    the original text by offset.
    """
    masked = _mask_safe_remote_uris(value)
    spans: list[tuple[int, int, str]] = []
    for pattern, placeholder in (
        (_LOCAL_FILE_URI, PATH_PLACEHOLDER),
        (_WINDOWS_DRIVE_PATH, PATH_PLACEHOLDER),
        (_HOST_PATH_TEXT, PATH_PLACEHOLDER),
        (_ENCODED_PATH, PATH_PLACEHOLDER),
        (_SECRET_BASENAME, SECRET_PLACEHOLDER),
    ):
        spans.extend(
            (match.start(), match.end(), placeholder)
            for match in pattern.finditer(masked)
            if match.end() > match.start()
        )
    spans.extend(
        (match.start(), match.end(), SECRET_PLACEHOLDER)
        for match in _SECRET_TEXT.finditer(value)
        if match.end() > match.start()
    )
    if not spans:
        return value

    spans.sort(key=lambda span: (span[0], -span[1]))
    merged: list[tuple[int, int, str]] = []
    for start, end, placeholder in spans:
        if merged and start <= merged[-1][1]:
            # The outermost span wins: a secret file name inside a host path is
            # already gone with the path, and one placeholder reads better.
            last_start, last_end, last_placeholder = merged[-1]
            merged[-1] = (last_start, max(last_end, end), last_placeholder)
            continue
        merged.append((start, end, placeholder))

    out: list[str] = []
    cursor = 0
    for start, end, placeholder in merged:
        out.append(value[cursor:start])
        out.append(placeholder)
        cursor = end
    out.append(value[cursor:])
    return "".join(out)


def scope_relative_path(value: Any) -> bool:
    """True for a citation path that stays inside its Area scope."""
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_CHARS:
        return False
    if (
        "\\" in value
        or "\x00" in value
        or value.startswith("~")
        or re.match(r"(?i)[a-z][a-z0-9+.-]*:", value)
        or _SECRET_TEXT.search(value)
        or _SECRET_BASENAME.search(value)
    ):
        return False
    parts = value.split("/")
    if any(part in {"", ".", "..", "~"} for part in parts):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value


# ---------------------------------------------------------------------------
# Field sanitizers. Each takes (value, field path, tool) and returns the value
# the model may see, or raises UnsanitizablePayload.
# ---------------------------------------------------------------------------

Field = Callable[[Any, str, str], Any]


def _scalar(value: Any, path: str, tool: str) -> Any:
    """Product text or a plain scalar: redacted, then verified."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        raise UnsanitizablePayload(tool, path, "an unexpected value shape")
    redacted = redact_host_paths(value[:MAX_TEXT_CHARS])
    leak = host_path_leak(redacted)
    if leak is not None:
        raise UnsanitizablePayload(tool, path, leak)
    return redacted


def _ident(value: Any, path: str, tool: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise UnsanitizablePayload(tool, path, "an unexpected identifier")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise UnsanitizablePayload(tool, path, "an unexpected identifier") from exc


def _map(value: Any, path: str, tool: str) -> dict[str, Any]:
    """A bounded open-keyed record of scalars (counts, health, freshness)."""
    if not isinstance(value, Mapping):
        raise UnsanitizablePayload(tool, path, "an unexpected record shape")
    out: dict[str, Any] = {}
    for key, nested in value.items():
        if len(out) >= MAX_MAP_KEYS:
            log.warning("Master %s dropped extra keys from %s", tool, path)
            break
        name = str(key)
        if not _SAFE_KEY.match(name):
            log.warning("Master %s dropped unsafe key in %s", tool, path)
            continue
        if isinstance(nested, (Mapping, list, tuple)):
            log.warning(
                "Master %s dropped undeclared nested value %s.%s", tool, path, name
            )
            continue
        out[name] = _scalar(nested, f"{path}.{name}", tool)
    return out


def _object(fields: dict[str, Field]) -> Field:
    """Allowlist record: declared fields survive, everything else is dropped."""

    def sanitize(value: Any, path: str, tool: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise UnsanitizablePayload(tool, path, "an unexpected record shape")
        out: dict[str, Any] = {}
        for key, nested in value.items():
            name = str(key)
            field = fields.get(name)
            if field is None:
                log.warning(
                    "Master %s dropped undeclared field %s.%s", tool, path, name
                )
                continue
            out[name] = field(nested, f"{path}.{name}", tool)
        return out

    return sanitize


def _array(item: Field, *, limit: int = MAX_ARRAY_ITEMS) -> Field:
    def sanitize(value: Any, path: str, tool: str) -> list[Any]:
        if not isinstance(value, (list, tuple)):
            raise UnsanitizablePayload(tool, path, "an unexpected list shape")
        if len(value) > limit:
            log.warning("Master %s truncated %s to %s items", tool, path, limit)
        return [
            item(nested, f"{path}[{index}]", tool)
            for index, nested in enumerate(value[:limit])
        ]

    return sanitize


def _citation(value: Any, path: str, tool: str) -> dict[str, Any]:
    """A validated scope-relative source citation - the narrow path exception."""
    if not isinstance(value, Mapping):
        raise UnsanitizablePayload(tool, path, "an unexpected citation shape")
    if value.get("path_kind") != "scope_relative":
        raise UnsanitizablePayload(tool, f"{path}.path_kind", "an unverified citation")
    if not scope_relative_path(value.get("path")):
        raise UnsanitizablePayload(tool, f"{path}.path", _SCOPE_REASON)
    out: dict[str, Any] = {
        "path": str(value["path"]),
        "path_kind": "scope_relative",
    }
    if "location" in value:
        out["location"] = _scalar(value["location"], f"{path}.location", tool)
    return out


_ERROR = _object(
    {
        "code": _scalar,
        "message": _scalar,
        "next_step": _scalar,
    }
)
_CONTAINER = _object(
    {
        "id": _ident,
        "slug": _scalar,
        "name": _scalar,
        "identity": _scalar,
        "summary": _scalar,
        "last_activity_at": _scalar,
        "live": _map,
        "areas": _map,
        "health": _map,
        "target_areas": _array(
            _object({"id": _ident, "kind": _scalar, "label": _scalar})
        ),
    }
)
_TASK = _object(
    {
        "id": _ident,
        "title": _scalar,
        "status": _scalar,
        "container_id": _ident,
        "area_id": _ident,
        "blocked_reason": _scalar,
        "created_at": _scalar,
        "updated_at": _scalar,
        "created": _scalar,
        "started": _scalar,
        "error": _ERROR,
    }
)
_GRAPH_ITEM = _object(
    {
        "id": _scalar,
        "label": _scalar,
        "type": _scalar,
        "distance": _scalar,
        "citations": _array(_citation),
        "provenance": _array(_scalar),
        "relations": _array(
            _object(
                {
                    "direction": _scalar,
                    "relation": _scalar,
                    "node_id": _scalar,
                    "provenance": _scalar,
                }
            )
        ),
    }
)
#: query_context returns one record per layer, and each layer carries a
#: different item shape. Dispatching on the layer name keeps every item
#: allowlisted instead of falling back to a permissive "any record".
_LAYER_ITEMS: dict[str, Field] = {
    "fleet": _CONTAINER,
    "live": _TASK,
    "knowledge": _GRAPH_ITEM,
    "code": _GRAPH_ITEM,
}
_LAYER_FIELDS: dict[str, Field] = {
    "layer": _scalar,
    "available": _scalar,
    "source": _scalar,
    "error": _ERROR,
    "scope": _object(
        {
            "container_id": _ident,
            "container_slug": _scalar,
            "kind": _scalar,
            "area_id": _ident,
        }
    ),
    "generation": _scalar,
    "freshness": _map,
    "limits": _map,
    "citations": _array(_citation),
    "provenance": _array(_object({"kind": _scalar, "edge_count": _scalar})),
    "independent_of_graphs": _scalar,
    "truncated": _scalar,
    "elapsed_ms": _scalar,
}


_LAYER_SHAPES: dict[str, Field] = {
    name: _object({**_LAYER_FIELDS, "items": _array(item)})
    for name, item in _LAYER_ITEMS.items()
}


def _layer(value: Any, path: str, tool: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UnsanitizablePayload(tool, path, "an unexpected layer shape")
    shape = _LAYER_SHAPES.get(str(value.get("layer") or ""))
    if shape is None:
        raise UnsanitizablePayload(tool, f"{path}.layer", "an unknown context layer")
    return shape(value, path, tool)


_TOOL_RESULTS: dict[str, Field] = {
    "list_containers": _object({"containers": _array(_CONTAINER)}),
    "get_container": _object({"container": _CONTAINER}),
    "get_live_state": _object({"counts": _map, "tasks": _array(_TASK)}),
    "list_tasks": _object({"tasks": _array(_TASK)}),
    "list_task_agents": _object(
        {
            "task_agents": _array(
                _object(
                    {
                        "id": _ident,
                        "name": _scalar,
                        "runner_id": _scalar,
                        "is_default": _scalar,
                    }
                )
            )
        }
    ),
    "list_recipes": _object(
        {
            "recipes": _array(
                _object(
                    {
                        "id": _ident,
                        "container_id": _ident,
                        "name": _scalar,
                        "description": _scalar,
                        "category": _scalar,
                        "engine": _scalar,
                    }
                )
            )
        }
    ),
    "query_context": _object(
        {
            "available": _scalar,
            "code": _scalar,
            "message": _scalar,
            "query": _scalar,
            "layers": _array(_scalar),
            "results": _array(_layer),
            "policy": _map,
            "budgets": _map,
        }
    ),
    "delegate_tasks": _object({"tasks": _array(_TASK)}),
    "start_tasks": _object({"tasks": _array(_TASK)}),
    "create_attention": _object(
        {
            "attention_id": _ident,
            "decision_id": _ident,
            "task_id": _ident,
            "state": _scalar,
            "created_at": _scalar,
        }
    ),
}


def sanitize_tool_result(tool: str, payload: Any) -> dict[str, Any]:
    """Return the model-visible form of one tool payload.

    Raises :class:`UnsanitizablePayload` when the payload has no declared shape
    or when a declared field still carries a host path after redaction.
    """
    shape = _TOOL_RESULTS.get(tool)
    if shape is None:
        raise UnsanitizablePayload(tool, "result", "no server-owned result shape")
    return shape(payload, "result", tool)


def sanitize_runner_message(tool: str | None, message: Any) -> str:
    """Redact one broker error message before the model can read it.

    Error strings are model-visible too - they quote schema failures, product
    errors, and boundary reasons that can embed a path.
    """
    text = str(message or "")[:MAX_TEXT_CHARS]
    redacted = redact_host_paths(text)
    if host_path_leak(redacted) is not None:
        log.warning("Master %s refused its own error text", tool or "tool")
        return (
            "This request failed and its message could not be shown safely. "
            f"{_NEXT_STEP}"
        )
    return redacted
