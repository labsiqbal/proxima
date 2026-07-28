"""Server-managed Graphify MCP fixed to exactly one Code graph Area.

Repo Task-agent homes receive this entry. Arbitrary ``project_path`` is ignored
and cannot retarget another Area's graph. The process reads only the graph path
Proxima places in ``PROXIMA_CODE_GRAPH_PATH``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _fixed_graph_path() -> Path:
    raw = (os.environ.get("PROXIMA_CODE_GRAPH_PATH") or "").strip()
    if not raw:
        raise SystemExit("PROXIMA_CODE_GRAPH_PATH is required")
    path = Path(raw)
    if path.is_symlink() or not path.is_file():
        raise SystemExit("fixed Code graph path is missing or not a regular file")
    return path.resolve(strict=True)


def _load_graph(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("graph root must be an object")
    return data


def _query(data: dict[str, Any], question: str, *, depth: int, limit: int) -> str:
    import re
    from collections import deque

    nodes = data.get("nodes") or []
    links = data.get("links") or data.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(links, list):
        return "graph is unavailable"
    by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id") is not None
    }
    terms = [
        token
        for token in re.findall(r"\w+", (question or "").casefold(), flags=re.UNICODE)
        if token
    ]
    scored: list[tuple[int, str]] = []
    for node_id, node in by_id.items():
        label = str(node.get("label") or node_id).casefold()
        source = str(node.get("source_file") or "").casefold()
        score = 0
        joined = " ".join(terms)
        if joined and joined in label:
            score += 100
        for term in terms:
            if term in label:
                score += 20
            if term in source:
                score += 5
        if score:
            scored.append((score, node_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    seeds = [node_id for _, node_id in scored[:5]]
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for link in links:
        if not isinstance(link, dict):
            continue
        source = str(link.get("source"))
        target = str(link.get("target"))
        if source in adjacency and target in adjacency:
            adjacency[source].append(target)
            adjacency[target].append(source)
    selected: list[str] = []
    distance: dict[str, int] = {}
    pending: deque[str] = deque()
    for seed in seeds:
        if seed not in distance:
            distance[seed] = 0
            pending.append(seed)
    while pending and len(selected) < max(1, limit):
        node_id = pending.popleft()
        selected.append(node_id)
        if distance[node_id] >= max(1, depth):
            continue
        for neighbor in adjacency.get(node_id, []):
            if neighbor not in distance:
                distance[neighbor] = distance[node_id] + 1
                pending.append(neighbor)
    lines = [f"Fixed Code graph query for: {question.strip() or '(empty)'}"]
    for node_id in selected:
        node = by_id[node_id]
        label = node.get("label") or node_id
        source = node.get("source_file") or ""
        lines.append(f"- {label} ({source})")
    if len(selected) <= 1 and not seeds:
        lines.append("(no matching nodes)")
    return "\n".join(lines)


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("utf-8", "replace").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    payload = json.loads(body.decode("utf-8"))
    return payload if isinstance(payload, dict) else None


def _write_message(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(
        f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw
    )
    sys.stdout.buffer.flush()


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "query_graph",
            "description": (
                "Search this Task's fixed Code graph. project_path is not accepted; "
                "the server is locked to the Task's selected Area."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 6},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    # Accepted only so hostile clients that still send it are ignored.
                    "project_path": {
                        "type": "string",
                        "description": "Ignored. This MCP is fixed to one Area.",
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        }
    ]


def main() -> None:
    graph_path = _fixed_graph_path()
    while True:
        message = _read_message()
        if message is None:
            return
        method = str(message.get("method") or "")
        req_id = message.get("id")
        if method == "initialize":
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "proxima-code-graph",
                            "version": "1",
                        },
                    },
                }
            )
            continue
        if method == "notifications/initialized":
            continue
        if method == "tools/list":
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": _tools()},
                }
            )
            continue
        if method == "tools/call":
            params = message.get("params") or {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            # Fail closed: never honor an alternate project_path.
            arguments.pop("project_path", None)
            if name != "query_graph":
                text = f"Unknown tool: {name}"
            else:
                try:
                    data = _load_graph(graph_path)
                    text = _query(
                        data,
                        str(arguments.get("question") or ""),
                        depth=int(arguments.get("depth") or 2),
                        limit=int(arguments.get("limit") or 40),
                    )
                except Exception as exc:
                    text = f"Error executing query_graph: {exc}"
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": text.startswith("Error ") or text.startswith("Unknown "),
                    },
                }
            )
            continue
        if req_id is not None:
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
