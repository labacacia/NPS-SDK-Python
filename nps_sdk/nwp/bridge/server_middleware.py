# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Pure-ASGI middleware exposing inbound MCP/A2A Bridge server adapters
(port of .NET ``BridgeServerMiddleware``).

Endpoints (under ``options.path_prefix``):

  POST /mcp                       — MCP JSON-RPC (optional GET .../mcp/sse for SSE)
  POST /a2a                       — A2A JSON-RPC
  GET  /.well-known/agent.json    — A2A AgentCard
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from nps_sdk.nwp import http_headers
from nps_sdk.nwp.bridge import json_rpc
from nps_sdk.nwp.bridge.a2a_server import A2aServerBridge
from nps_sdk.nwp.bridge.json_rpc import (
    BridgeJsonRpcErrorCodes,
    BridgeJsonRpcRequest,
    BridgeJsonRpcResponse,
)
from nps_sdk.nwp.bridge.mcp_server import McpServerBridge
from nps_sdk.nwp.bridge.server_options import (
    BridgeServerActionInvoker,
    BridgeServerOptions,
)

_Dispatch = Callable[[BridgeJsonRpcRequest], Awaitable[BridgeJsonRpcResponse]]


class _PayloadTooLarge(Exception):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"Bridge server request body exceeds the configured {max_bytes} byte limit.")


class _DispatchTimeout(Exception):
    def __init__(self, timeout_ms: int) -> None:
        super().__init__(f"Bridge server dispatch timed out after {timeout_ms}ms.")


class BridgeServerMiddleware:
    """Pure-ASGI middleware hosting inbound MCP/A2A Bridge server adapters."""

    def __init__(
        self,
        options: BridgeServerOptions,
        mcp: McpServerBridge | None = None,
        a2a: A2aServerBridge | None = None,
    ) -> None:
        self._options = options
        invoker = BridgeServerActionInvoker(options)
        self._mcp = mcp or McpServerBridge(options, invoker)
        self._a2a = a2a or A2aServerBridge(options, invoker)
        self._prefix = options.path_prefix.rstrip("/")

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return

        path = scope.get("path", "")
        if not path.startswith(self._prefix):
            await self._send(send, 404, b"", "application/json")
            return

        sub = path[len(self._prefix):]
        method = scope.get("method", "GET")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        if _matches(sub, self._options.mcp_path) or _matches(sub, _append(self._options.mcp_path, "/sse")):
            use_sse = _is_sse_request(headers) or _matches(sub, _append(self._options.mcp_path, "/sse"))
            await self._handle_mcp(scope, receive, send, headers, method, use_sse)
        elif _matches(sub, self._options.a2a_path):
            await self._handle_a2a(scope, receive, send, headers, method)
        elif _matches(sub, self._options.a2a_agent_card_path):
            await self._handle_agent_card(scope, send, method)
        else:
            await self._send(send, 404, b"", "application/json")

    # ── MCP ──────────────────────────────────────────────────────────────────────

    async def _handle_mcp(self, scope, receive, send, headers, method, use_sse) -> None:
        if method == "GET" and use_sse:
            body = f"event: endpoint\ndata: {_join(self._options.path_prefix, self._options.mcp_path)}\n\n"
            await self._send(send, 200, body.encode("utf-8"), "text/event-stream")
            return

        if method != "POST":
            await self._send(send, 405, b"", "application/json")
            return

        authorized, message = await self._authorize(headers)
        if not authorized:
            await self._write_jsonrpc_error(send, 401, BridgeJsonRpcErrorCodes.INVALID_REQUEST, message)
            return

        status, response = await self._read_and_dispatch(scope, receive, headers, self._mcp.dispatch)
        if use_sse:
            payload = json.dumps(response.to_dict(), separators=(",", ":"), ensure_ascii=False)
            await self._send(send, status, f"event: message\ndata: {payload}\n\n".encode("utf-8"), "text/event-stream")
        else:
            await self._write_json(send, status, response.to_dict())

    # ── A2A ──────────────────────────────────────────────────────────────────────

    async def _handle_a2a(self, scope, receive, send, headers, method) -> None:
        if method != "POST":
            await self._send(send, 405, b"", "application/json")
            return

        authorized, message = await self._authorize(headers)
        if not authorized:
            await self._write_jsonrpc_error(send, 401, BridgeJsonRpcErrorCodes.INVALID_REQUEST, message)
            return

        status, response = await self._read_and_dispatch(scope, receive, headers, self._a2a.dispatch)
        await self._write_json(send, status, response.to_dict())

    async def _handle_agent_card(self, scope, send, method) -> None:
        if method != "GET":
            await self._send(send, 405, b"", "application/json")
            return

        scheme = scope.get("scheme", "http")
        host = ""
        for k, v in scope.get("headers", []):
            if k == b"host":
                host = v.decode("latin-1")
                break
        endpoint = f"{scheme}://{host}{_join(self._options.path_prefix, self._options.a2a_path)}"
        await self._write_json(send, 200, self._a2a.build_agent_card(endpoint))

    # ── Read + dispatch ───────────────────────────────────────────────────────────

    async def _read_and_dispatch(self, scope, receive, headers, dispatch: _Dispatch) -> tuple[int, BridgeJsonRpcResponse]:
        try:
            request = await self._read_request(scope, receive, headers)
            if request is None:
                return 400, json_rpc.error(None, BridgeJsonRpcErrorCodes.INVALID_REQUEST, "JSON-RPC request is required.")
            return 200, await self._dispatch_with_timeout(request, dispatch)
        except _PayloadTooLarge as exc:
            return 413, json_rpc.error(None, BridgeJsonRpcErrorCodes.INVALID_REQUEST, str(exc))
        except _DispatchTimeout as exc:
            return 504, json_rpc.error(None, BridgeJsonRpcErrorCodes.UPSTREAM_ERROR, str(exc))
        except json.JSONDecodeError as exc:
            return 400, json_rpc.error(None, BridgeJsonRpcErrorCodes.PARSE_ERROR, str(exc))
        except Exception:  # noqa: BLE001
            return 500, json_rpc.error(None, BridgeJsonRpcErrorCodes.INTERNAL_ERROR, "Bridge server request failed.")

    async def _read_request(self, scope, receive, headers) -> BridgeJsonRpcRequest | None:
        max_bytes = self._options.max_request_body_bytes
        content_length = headers.get("content-length")
        if max_bytes > 0 and content_length is not None and int(content_length) > max_bytes:
            raise _PayloadTooLarge(max_bytes)

        chunks: list[bytes] = []
        total = 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                break
            chunk = msg.get("body", b"")
            total += len(chunk)
            if max_bytes > 0 and total > max_bytes:
                raise _PayloadTooLarge(max_bytes)
            chunks.append(chunk)
            if not msg.get("more_body", False):
                break

        raw = b"".join(chunks)
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return BridgeJsonRpcRequest.from_dict(data)

    async def _dispatch_with_timeout(self, request: BridgeJsonRpcRequest, dispatch: _Dispatch) -> BridgeJsonRpcResponse:
        timeout_ms = self._options.dispatch_timeout_ms
        if timeout_ms == 0:
            return await dispatch(request)
        try:
            return await asyncio.wait_for(dispatch(request), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            raise _DispatchTimeout(timeout_ms)

    # ── Auth ──────────────────────────────────────────────────────────────────────

    async def _authorize(self, headers: dict[str, str]) -> tuple[bool, str]:
        if not self._options.require_auth:
            return True, ""

        agent = headers.get(http_headers.AGENT.lower())
        if not agent or not agent.strip():
            return False, "A valid X-NWP-Agent NID is required."

        agent = agent.strip()
        if not _is_valid_agent_nid(agent):
            return False, "A valid X-NWP-Agent NID is required."

        if self._options.verify_agent is None:
            return False, "Bridge server agent verifier is required."

        if not await self._options.verify_agent(agent, headers):
            return False, "X-NWP-Agent was rejected by Bridge server policy."

        return True, ""

    # ── Response helpers ──────────────────────────────────────────────────────────

    async def _send(self, send: Callable, status: int, body: bytes, content_type: str) -> None:
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", content_type.encode("latin-1"))]})
        await send({"type": "http.response.body", "body": body})

    async def _write_json(self, send: Callable, status: int, body: Any) -> None:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        await self._send(send, status, payload, "application/json")

    async def _write_jsonrpc_error(self, send: Callable, status: int, code: int, message: str) -> None:
        await self._write_json(send, status, json_rpc.error(None, code, message).to_dict())


def _is_valid_agent_nid(nid: str) -> bool:
    prefix = "urn:nps:agent:"
    if not nid.startswith(prefix) or len(nid) > 512:
        return False
    rest = nid[len(prefix):]
    sep = rest.find(":")
    if sep <= 0 or sep == len(rest) - 1:
        return False
    domain = rest[:sep]
    identifier = rest[sep + 1:]
    return all(_is_domain_char(c) for c in domain) and all(_is_identifier_char(c) for c in identifier)


def _is_domain_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in (".", "-"))


def _is_identifier_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in (".", "_", "-", "~", ":", "@", "/"))


def _matches(actual: str, expected: str) -> bool:
    normalized = expected if expected.startswith("/") else "/" + expected
    return actual.lower() == normalized.lower() or actual.lower() == (normalized + "/").lower()


def _append(path: str, suffix: str) -> str:
    return path.rstrip("/") + suffix


def _join(prefix: str, path: str) -> str:
    left = prefix.rstrip("/")
    right = path if path.startswith("/") else "/" + path
    return right if not left else left + right


def _is_sse_request(headers: dict[str, str]) -> bool:
    accept = headers.get("accept", "")
    return "text/event-stream" in accept.lower()
