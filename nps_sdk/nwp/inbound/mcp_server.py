# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Inbound MCP server surface of a Bridge Node (NWP §2.1 inbound profile, §16.1.2).

Projects the NWP nodes behind one or more backends onto MCP: Memory / Complex Nodes
become resources, Action / Complex Nodes become tools. Unlike both pre-CR-0010
implementations it maps NPS failures onto JSON-RPC errors per §16.3 instead of
returning them as a "successful" result carrying ``isError: true``.
"""

from __future__ import annotations

import json
from typing import Any, Sequence
from urllib.parse import urlsplit

from nps_sdk.core.status_codes import NPS_SERVER_UNSUPPORTED
from nps_sdk.nwp.inbound.backend import (
    OPEN_OBJECT_SCHEMA,
    NwpActionDescriptor,
    NwpBackend,
    NwpResult,
)
from nps_sdk.nwp.inbound.error_map import (
    BridgeErrorCodes,
    BridgeErrorMap,
    BridgeJsonRpcErrorCodes,
)
from nps_sdk.nwp.inbound.jsonrpc import (
    BridgeJsonRpcRequest,
    BridgeJsonRpcResponse,
    jsonrpc_error,
    jsonrpc_success,
)
from nps_sdk.nwp.inbound.options import BridgeInboundOptions

__all__ = ["McpToolName", "McpInboundServer"]

_SEPARATOR = "__"


class McpToolName:
    """Encodes an NWP ``(node, action_id)`` pair as a protocol-safe MCP tool name.

    MCP tool names are a flat namespace, so the node name is folded in with a ``__``
    separator; dots in an action id — legal in NPS, awkward in some MCP clients —
    become underscores.

    **Encoding only.** There is deliberately no decode: the transform is lossy (a dot
    and an underscore both map to underscore; a node name may itself contain ``__``),
    so an inverse would be ambiguous. Callers resolve a tool name by *re-encoding* each
    candidate and comparing, never by splitting the incoming string.
    """

    @staticmethod
    def encode(node_name: str, action_id: str) -> str:
        return f"{McpToolName._sanitize(node_name)}{_SEPARATOR}" \
               f"{McpToolName.encode_action_segment(action_id)}"

    @staticmethod
    def encode_action_segment(action_id: str) -> str:
        """Just the action segment — the bare form a pre-CR-0010 client saw."""
        return McpToolName._sanitize(action_id).replace(".", "_")

    @staticmethod
    def _sanitize(value: str) -> str:
        if not (value or "").strip():
            return "node"
        cleaned = "".join(
            ch if (ch.isalnum() or ch in "_-.") else "_" for ch in value.strip())
        cleaned = cleaned.strip("_")
        return cleaned if cleaned.strip() else "node"


class McpInboundServer:
    """Serves the full MCP method set over any set of NWP backends."""

    #: MCP methods a conformant inbound Bridge Node MUST serve (NWP §16.1.2 MUST-3).
    #: Serving ``resources/*`` over an EMPTY set is conformant — the requirement is on
    #: the methods, not on a Memory Node existing.
    REQUIRED_METHODS: tuple[str, ...] = (
        "initialize", "ping", "tools/list", "tools/call",
        "resources/list", "resources/read",
    )

    def __init__(self, options: BridgeInboundOptions, backends: Sequence[NwpBackend]) -> None:
        self._options = options
        self._backends = list(backends)

    # ── dispatch ─────────────────────────────────────────────────────────────

    async def dispatch(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        """Dispatch one MCP JSON-RPC request."""
        if request is None:
            raise ValueError("request is required")

        # §16.1.2 MUST-5, checked first thing: reject a protocol this Bridge Node did
        # not declare in bridge_inbound_protocols rather than serving it anyway.
        if not self._options.serves_inbound("mcp"):
            return jsonrpc_error(
                request,
                BridgeErrorMap.to_json_rpc(NPS_SERVER_UNSUPPORTED),
                'This Bridge Node does not declare "mcp" in bridge_inbound_protocols.',
                {"error": BridgeErrorCodes.DIRECTION_UNSUPPORTED,
                 "hint": self._options.declared_protocols_hint()})

        try:
            if request.method == "initialize":
                return jsonrpc_success(request, self._initialize())
            if request.method == "ping":
                return jsonrpc_success(request, {})
            if request.method == "tools/list":
                return jsonrpc_success(request, await self._list_tools())
            if request.method == "tools/call":
                return await self._call_tool(request)
            if request.method == "resources/list":
                return jsonrpc_success(request, await self._list_resources())
            if request.method == "resources/read":
                return await self._read_resource(request)
            return jsonrpc_error(
                request, BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND,
                f"MCP method '{request.method}' is not supported by this Bridge Node.",
                {"error": BridgeErrorCodes.DIRECTION_UNSUPPORTED})
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            return jsonrpc_error(request, BridgeJsonRpcErrorCodes.INVALID_PARAMS, str(exc))

    # ── initialize ───────────────────────────────────────────────────────────

    def _initialize(self) -> dict[str, Any]:
        return {
            "serverInfo": {
                "name": self._options.server_name,
                "version": self._options.server_version,
            },
            # Both capabilities are ALWAYS advertised: §16.1.2 requires the resource
            # methods to be served even when no Memory Node sits behind this Bridge.
            "capabilities": {"tools": {}, "resources": {}},
        }

    # ── tools/list ───────────────────────────────────────────────────────────

    async def _list_tools(self) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        for backend in self._backends:
            descriptor = await backend.get_descriptor()
            if not descriptor.is_invokable:
                continue
            for action in await backend.get_actions():
                tools.append({
                    "name": McpToolName.encode(descriptor.name, action.action_id),
                    "description": action.description,
                    "inputSchema": action.input_schema or dict(OPEN_OBJECT_SCHEMA),
                })
        return {"tools": tools}

    # ── tools/call ───────────────────────────────────────────────────────────

    async def _call_tool(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        params = request.params if isinstance(request.params, dict) else None
        name = (params or {}).get("name")
        if not isinstance(name, str) or not name.strip():
            return jsonrpc_error(request, BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                                 "MCP tools/call requires params.name.")

        resolved, candidates = await self.resolve_tool(name)
        if resolved is None:
            data: dict[str, Any] = {
                "error": BridgeErrorCodes.SERVER_TOOL_NOT_FOUND, "tool": name}
            if candidates:
                # TC-N2-BridgeIn-04 wants the ambiguity rejection to name both
                # qualified candidates; the .NET message names only the request.
                data["candidates"] = candidates
            return jsonrpc_error(
                request,
                # §16.3: an unknown tool is a missing *method* to an MCP client. The
                # retired -32002 is deliberately not reused.
                BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND,
                f"MCP tool '{name}' is not exposed by this Bridge Node.", data)

        backend, action = resolved
        result = await backend.invoke(action.action_id, (params or {}).get("arguments"),
                                      action.async_)
        return self._to_tool_call_response(request, result)

    async def resolve_tool(
        self, tool_name: str,
    ) -> tuple[tuple[NwpBackend, NwpActionDescriptor] | None, list[str]]:
        """Resolve an MCP tool name. *Canonical on output, forgiving on input.*

        ``tools/list`` always emits the qualified ``node__action`` form — it must, since
        MCP tool names are a flat namespace and a Bridge may front several nodes. A bare
        action id is also accepted when it resolves unambiguously, so clients written
        against the pre-CR-0010 in-process Bridge keep working.

        :returns: ``(match_or_None, qualified_candidate_names)``. The candidate list is
            non-empty only on an ambiguous bare-id request, so the caller can name both
            qualified candidates in the rejection.
        """
        unqualified: list[tuple[NwpBackend, NwpActionDescriptor, str]] = []

        for backend in self._backends:
            descriptor = await backend.get_descriptor()
            if not descriptor.is_invokable:
                continue
            for action in await backend.get_actions():
                qualified = McpToolName.encode(descriptor.name, action.action_id)
                # Match by re-encoding, never by decoding the incoming name.
                if qualified.lower() == tool_name.lower():
                    return (backend, action), []
                if (action.action_id.lower() == tool_name.lower()
                        or McpToolName.encode_action_segment(
                            action.action_id).lower() == tool_name.lower()):
                    unqualified.append((backend, action, qualified))

        if len(unqualified) == 1:
            backend, action, _ = unqualified[0]
            return (backend, action), []
        # Two nodes exposing the same action id must be disambiguated by the caller,
        # not guessed at here.
        return None, sorted(q for _, _, q in unqualified)

    @staticmethod
    def _to_tool_call_response(
        request: BridgeJsonRpcRequest, result: NwpResult,
    ) -> BridgeJsonRpcResponse:
        """Turn an NWP result into an MCP ``tools/call`` response.

        The split here is the §16.3 rule both predecessors got wrong: an auth, limit or
        unsupported failure MUST surface as a JSON-RPC *error*. Returning it as a
        successful result with ``isError: true`` lets an MCP client mistake a 403 for a
        tool that merely returned unhappy text.
        """
        if not result.ok and BridgeErrorMap.must_be_protocol_error(result.nps_status):
            return jsonrpc_error(
                request,
                BridgeErrorMap.to_json_rpc(result.nps_status),
                result.message or result.nps_status or "NWP dispatch failed.",
                {"status": result.nps_status, "error": result.nwp_error})

        text = (result.raw_payload_json() if result.ok
                else json.dumps(result.failure_payload(), separators=(",", ":"),
                                ensure_ascii=False))
        return jsonrpc_success(request, {
            "isError": not result.ok,
            "content": [{"type": "text", "text": text}],
        })

    # ── resources/list ───────────────────────────────────────────────────────

    async def _list_resources(self) -> dict[str, Any]:
        resources: list[dict[str, Any]] = []
        for backend in self._backends:
            descriptor = await backend.get_descriptor()
            if not descriptor.is_queryable:
                continue
            resources.append({
                "uri": f"nwp://{descriptor.name}/",
                "name": descriptor.display_name or descriptor.name,
                "description": descriptor.description or (
                    f"NWP {descriptor.role.value} Node '{descriptor.name}' — read to query."),
                "mimeType": "application/json",
            })
        # An empty set is conformant: §16.1.2 requires the method to be served, not
        # that a Memory Node exist behind it.
        return {"resources": resources}

    # ── resources/read ───────────────────────────────────────────────────────

    async def _read_resource(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        params = request.params if isinstance(request.params, dict) else None
        uri = (params or {}).get("uri")
        if not isinstance(uri, str) or not uri.strip():
            return jsonrpc_error(request, BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                                 "MCP resources/read requires params.uri.")

        parsed = urlsplit(uri)
        if parsed.scheme != "nwp" or not parsed.hostname:
            return jsonrpc_error(
                request, BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                f"Resource URI '{uri}' must be of the form nwp://<node>/.")

        backend = await self._resolve_backend(parsed.hostname)
        if backend is None:
            return jsonrpc_error(
                request,
                # §16.3: an unknown *resource* is a bad argument, not a missing method.
                BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                f"Resource '{uri}' is not exposed by this Bridge Node.",
                {"error": BridgeErrorCodes.SERVER_TOOL_NOT_FOUND, "uri": uri})

        result = await backend.query({"limit": self._options.resource_read_limit})
        if not result.ok:
            return jsonrpc_error(
                request,
                BridgeErrorMap.to_json_rpc(result.nps_status, resource_read=True),
                result.message or result.nps_status or "NWP query failed.",
                {"status": result.nps_status, "error": result.nwp_error})

        return jsonrpc_success(request, {
            "contents": [{
                "uri": uri,
                "mimeType": "application/json",
                "text": result.raw_payload_json(),
            }],
        })

    async def _resolve_backend(self, node_name: str) -> NwpBackend | None:
        for backend in self._backends:
            descriptor = await backend.get_descriptor()
            if descriptor.name.lower() == node_name.lower():
                return backend
        return None

    # ── stdio transport ──────────────────────────────────────────────────────

    async def run_stdio(self, reader: Any, writer: Any) -> None:
        """Serve MCP over stdio: line-delimited JSON-RPC in, one line out per request.

        This is the transport most MCP clients launch a server with, so it is part of
        the inbound profile, not an extra. *reader* needs ``readline()`` and *writer*
        ``write()`` / ``flush()`` — a pair of text streams, or any duck-typed pair.
        """
        while True:
            line = reader.readline()
            if line is None or line == "":
                break                       # EOF ends the loop
            if not line.strip():
                continue                    # blank lines are skipped

            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    response = jsonrpc_error(
                        None, BridgeJsonRpcErrorCodes.INVALID_REQUEST,
                        "JSON-RPC request is required.")
                else:
                    response = await self.dispatch(BridgeJsonRpcRequest.from_dict(data))
            except json.JSONDecodeError as exc:
                response = jsonrpc_error(None, BridgeJsonRpcErrorCodes.PARSE_ERROR, str(exc))
            except ValueError as exc:
                response = jsonrpc_error(None, BridgeJsonRpcErrorCodes.INVALID_REQUEST, str(exc))

            writer.write(json.dumps(response.to_dict(), separators=(",", ":")) + "\n")
            writer.flush()
