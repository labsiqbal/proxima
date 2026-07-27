"""Fail-closed model-request firewall for Codex Master.

Codex app-server 0.145 always registers a harmless but runner-native
``update_plan`` tool. Master is stricter: the model may receive only Proxima's
dynamic product tools. This loopback proxy removes every non-broker tool from
the final Responses request, verifies the exact resulting allowlist, and only
then forwards the request to the provider.

The proxy is deliberately private to one Codex process. Its unguessable URL is
written only to that process's trusted configuration, it accepts bounded
requests, and it never logs authorization headers or request bodies.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import re
import secrets
from contextlib import suppress
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx

_MAX_HEADER_BYTES = 64 * 1024
_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_ABSOLUTE_PATH = re.compile(
    r"""(?:^|[\s"'(])(?:/[^\s"'<>]+|[A-Za-z]:\\[^\s"'<>]+)"""
)
_MASTER_DEVELOPER_TEXT = (
    "You are Proxima Master. Chat and call only the supplied Proxima product "
    "functions. You have no native tools or host access."
)


class MasterModelRequestError(RuntimeError):
    """A stable fail-closed request-firewall error."""


def _tool_name(spec: Any) -> str:
    if not isinstance(spec, dict):
        return ""
    name = spec.get("name")
    if isinstance(name, str):
        return name
    function = spec.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    return ""


def _provider_tool(spec: dict[str, Any]) -> dict[str, Any]:
    if (
        set(spec) != {"type", "name", "description", "inputSchema"}
        or spec.get("type") != "function"
        or not isinstance(spec.get("name"), str)
        or not isinstance(spec.get("description"), str)
        or not isinstance(spec.get("inputSchema"), dict)
    ):
        raise MasterModelRequestError(
            "Master product tool schema is malformed"
        )
    return {
        "type": "function",
        "name": spec["name"],
        "description": spec["description"],
        "strict": False,
        "parameters": spec["inputSchema"],
    }


def _string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _string_values(nested)


def reconstruct_developer_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Discard runner-generated developer text and install a path-free policy."""
    inputs = payload.get("input")
    if isinstance(inputs, list):
        retained = [
            item
            for item in inputs
            if not (
                isinstance(item, dict)
                and item.get("role") == "developer"
                and item.get("type") != "additional_tools"
            )
        ]
        insert_at = (
            1
            if retained
            and isinstance(retained[0], dict)
            and retained[0].get("type") == "additional_tools"
            else 0
        )
        retained.insert(
            insert_at,
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {"type": "input_text", "text": _MASTER_DEVELOPER_TEXT}
                ],
            },
        )
        payload["input"] = retained
    if "instructions" in payload:
        payload["instructions"] = _MASTER_DEVELOPER_TEXT
    return payload


def reconstruct_model_tools(
    payload: dict[str, Any],
    product_specs: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], set[str], set[str]]:
    """Replace Codex's tool set with exact server-owned Proxima schemas.

    Responses Lite carries tools in an ``additional_tools`` input item. The
    public Responses wire shape uses the top-level ``tools`` field. Supporting
    both here keeps the enforcement at the final provider request rather than
    relying on an app-server implementation detail.
    """
    canonical = [_provider_tool(spec) for spec in product_specs]
    allowed_names = {_tool_name(spec) for spec in canonical}
    if not allowed_names or len(allowed_names) != len(canonical):
        raise MasterModelRequestError(
            "Master product tool schema set is incomplete"
        )
    supplied_product: dict[str, dict[str, Any]] = {}
    removed: set[str] = set()
    used_lite_carrier = False
    used_top_level_carrier = "tools" in payload

    def discard(raw: Any) -> None:
        if not isinstance(raw, list):
            raise MasterModelRequestError("Model tool list is malformed")
        for spec in raw:
            name = _tool_name(spec)
            if not name:
                raise MasterModelRequestError("Model tool has no stable name")
            if name in supplied_product or name in removed:
                raise MasterModelRequestError(
                    f"Model tool list duplicates {name}"
                )
            if name in allowed_names:
                if not isinstance(spec, dict):
                    raise MasterModelRequestError(
                        f"Model tool schema changed for {name}"
                    )
                supplied_product[name] = spec
            else:
                removed.add(name)

    if "tools" in payload:
        discard(payload.pop("tools"))
    inputs = payload.get("input")
    if isinstance(inputs, list):
        retained = []
        for item in inputs:
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                discard(item.get("tools"))
                used_lite_carrier = True
                continue
            retained.append(item)
        payload["input"] = retained

    # If Codex supplied any broker schemas, it must have supplied the complete,
    # byte-for-byte equivalent set. A partial or rewritten broker contract is
    # rejected. Supplying none is allowed because this firewall is the trusted
    # source of the complete model-visible contract.
    if supplied_product:
        if set(supplied_product) != allowed_names:
            raise MasterModelRequestError(
                "Codex supplied an incomplete Master product tool set"
            )
        expected = {spec["name"]: spec for spec in canonical}
        for name, supplied in supplied_product.items():
            if supplied != expected[name]:
                raise MasterModelRequestError(
                    f"Codex changed the Master product schema for {name}"
                )

    if used_lite_carrier or (
        not used_top_level_carrier and isinstance(payload.get("input"), list)
    ):
        if not isinstance(payload.get("input"), list):
            raise MasterModelRequestError(
                "Responses Lite input is malformed"
            )
        payload["input"].insert(
            0,
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": canonical,
            },
        )
    else:
        payload["tools"] = canonical

    # Validate the reconstructed output rather than trusting our mutation path.
    carrier = (
        payload["input"][0]["tools"]
        if payload.get("input")
        and payload["input"][0].get("type") == "additional_tools"
        else payload.get("tools")
    )
    if carrier != canonical or {_tool_name(spec) for spec in carrier} != allowed_names:
        raise MasterModelRequestError(
            "Master product tool reconstruction failed"
        )
    return payload, allowed_names, removed


class CodexMasterModelProxy:
    """One authenticated loopback firewall for one restricted Codex process."""

    def __init__(self, *, protected_values: tuple[str, ...] = ()):
        self._protected_values = tuple(v for v in protected_values if v)
        self._product_specs: tuple[dict[str, Any], ...] = ()
        self._secret = secrets.token_urlsafe(32)
        self._server: asyncio.AbstractServer | None = None
        self._client: httpx.AsyncClient | None = None
        self.last_forwarded_tool_names: tuple[str, ...] = ()
        self.last_removed_tool_names: tuple[str, ...] = ()

    async def start(self) -> str:
        if self._server is not None:
            sockets = self._server.sockets or ()
            port = sockets[0].getsockname()[1]
            return f"http://127.0.0.1:{port}/{self._secret}/v1"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=20.0, read=None, write=30.0, pool=10.0
            ),
            follow_redirects=False,
        )
        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", 0
        )
        port = (self._server.sockets or ())[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/{self._secret}/v1"

    def set_product_tools(
        self,
        specs: list[dict[str, Any]],
        *,
        required_names: set[str],
    ) -> None:
        copied = tuple(
            json.loads(json.dumps(spec, separators=(",", ":")))
            for spec in specs
        )
        names = {_tool_name(spec) for spec in copied}
        if (
            not names
            or names != required_names
            or len(names) != len(copied)
        ):
            raise MasterModelRequestError(
                "Master product tool schema set is incomplete"
            )
        for spec in copied:
            _provider_tool(spec)
        self._product_specs = copied

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            method, target, headers, body = await self._read_request(reader)
            path = urlsplit(target).path
            prefix = f"/{self._secret}/v1/"
            if not path.startswith(prefix):
                raise MasterModelRequestError("Invalid Master proxy route")
            if method == "GET" and path.endswith("/responses"):
                await self._write_error(
                    writer, 426, "Master requires filtered HTTP transport"
                )
                return
            if method == "POST" and path.endswith("/responses"):
                body, headers = self._restrict_request(body, headers)
            await self._forward(method, target, headers, body, writer)
        except MasterModelRequestError as exc:
            await self._write_error(writer, 400, str(exc))
        except (httpx.HTTPError, asyncio.IncompleteReadError):
            await self._write_error(writer, 502, "Master model provider failed")
        except Exception:
            await self._write_error(writer, 500, "Master request firewall failed")
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str], bytes]:
        line = await reader.readline()
        if not line or len(line) > _MAX_HEADER_BYTES:
            raise MasterModelRequestError("Invalid Master proxy request")
        try:
            method, target, _version = line.decode("ascii").strip().split(" ", 2)
        except (UnicodeDecodeError, ValueError) as exc:
            raise MasterModelRequestError(
                "Invalid Master proxy request line"
            ) from exc
        if method not in {"GET", "POST"}:
            raise MasterModelRequestError("Master proxy method is not allowed")
        headers: dict[str, str] = {}
        header_bytes = len(line)
        while True:
            raw = await reader.readline()
            header_bytes += len(raw)
            if header_bytes > _MAX_HEADER_BYTES:
                raise MasterModelRequestError(
                    "Master proxy headers are too large"
                )
            if raw in {b"\r\n", b"\n", b""}:
                break
            try:
                key, value = raw.decode("latin-1").split(":", 1)
            except ValueError as exc:
                raise MasterModelRequestError(
                    "Invalid Master proxy header"
                ) from exc
            headers[key.strip().lower()] = value.strip()
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError as exc:
            raise MasterModelRequestError(
                "Invalid Master proxy content length"
            ) from exc
        if content_length < 0 or content_length > _MAX_REQUEST_BYTES:
            raise MasterModelRequestError(
                "Master model request exceeds the byte limit"
            )
        body = await reader.readexactly(content_length) if content_length else b""
        return method, target, headers, body

    def _restrict_request(
        self,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[bytes, dict[str, str]]:
        encoding = headers.get("content-encoding", "").lower()
        try:
            decoded = gzip.decompress(body) if encoding == "gzip" else body
            payload = json.loads(decoded)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MasterModelRequestError(
                "Master model request is not valid JSON "
                f"(encoding={encoding or 'identity'}, bytes={len(body)})"
            ) from exc
        if not isinstance(payload, dict):
            raise MasterModelRequestError(
                "Master model request must be an object"
            )
        payload, seen, removed = reconstruct_model_tools(
            payload, self._product_specs
        )
        payload = reconstruct_developer_context(payload)
        self._reject_protected_developer_context(payload, headers)
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if encoding == "gzip":
            encoded = gzip.compress(encoded)
        elif encoding:
            raise MasterModelRequestError(
                "Master model request encoding is unsupported"
            )
        self.last_forwarded_tool_names = tuple(sorted(seen))
        self.last_removed_tool_names = tuple(sorted(removed))
        return encoded, headers

    def _reject_protected_developer_context(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        protected = list(self._protected_values)
        authorization = headers.get("authorization", "")
        if authorization:
            protected.append(authorization.removeprefix("Bearer ").strip())
        for item in payload.get("input") or []:
            if not isinstance(item, dict) or item.get("role") != "developer":
                continue
            strings = tuple(_string_values(item))
            if any(
                secret and secret in text
                for secret in protected
                for text in strings
            ):
                raise MasterModelRequestError(
                    "Master developer context contains protected host data"
                )
            if item.get("type") != "additional_tools" and any(
                _ABSOLUTE_PATH.search(text) for text in strings
            ):
                raise MasterModelRequestError(
                    "Master developer context contains a filesystem path"
                )

    async def _forward(
        self,
        method: str,
        target: str,
        headers: dict[str, str],
        body: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        assert self._client is not None
        split = urlsplit(target)
        relative = split.path.split(f"/{self._secret}/v1", 1)[1]
        query = f"?{split.query}" if split.query else ""
        if headers.get("chatgpt-account-id"):
            upstream = (
                f"https://chatgpt.com/backend-api/codex{relative}{query}"
            )
        else:
            upstream = f"https://api.openai.com/v1{relative}{query}"
        forwarded_headers = {
            key: value
            for key, value in headers.items()
            if key not in _HOP_HEADERS
        }
        async with self._client.stream(
            method,
            upstream,
            headers=forwarded_headers,
            content=body or None,
        ) as response:
            reason = response.reason_phrase or "Response"
            writer.write(
                f"HTTP/1.1 {response.status_code} {reason}\r\n".encode("ascii")
            )
            for key, value in response.headers.multi_items():
                if key.lower() not in _HOP_HEADERS:
                    writer.write(
                        f"{key}: {value}\r\n".encode("latin-1")
                    )
            writer.write(b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
            await writer.drain()
            async for chunk in response.aiter_raw():
                if not chunk:
                    continue
                writer.write(f"{len(chunk):x}\r\n".encode("ascii"))
                writer.write(chunk)
                writer.write(b"\r\n")
                await writer.drain()
            writer.write(b"0\r\n\r\n")
            await writer.drain()

    @staticmethod
    async def _write_error(
        writer: asyncio.StreamWriter, status: int, message: str
    ) -> None:
        body = json.dumps(
            {"error": {"message": message}},
            separators=(",", ":"),
        ).encode("utf-8")
        writer.write(
            (
                f"HTTP/1.1 {status} Error\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        writer.write(body)
        with suppress(Exception):
            await writer.drain()
