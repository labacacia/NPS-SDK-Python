# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Inbound MCP adapter exposing local NPS actions as MCP tools
(port of .NET ``McpServerBridge``)."""
from __future__ import annotations

from typing import Any

from nps_sdk.ncp.frames import ErrorFrame
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.bridge import frame_json, json_rpc
from nps_sdk.nwp.bridge.errors import BridgeErrorCodes
from nps_sdk.nwp.bridge.json_rpc import (
    BridgeJsonRpcErrorCodes,
    BridgeJsonRpcRequest,
    BridgeJsonRpcResponse,
)
from nps_sdk.nwp.bridge.server_options import (
    BridgeServerAction,
    BridgeServerOptions,
    IBridgeServerActionInvoker,
)
from nps_sdk.nwp.bridge.server_types import McpServerProtocol


class McpServerBridge:
    """Inbound MCP adapter that exposes local NPS actions as MCP tools."""

    def __init__(self, options: BridgeServerOptions, invoker: IBridgeServerActionInvoker) -> None:
        self._options = options
        self._invoker = invoker

    async def dispatch(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        if request is None:
            raise ValueError("request must not be None")

        method = request.method
        if method == "initialize":
            return json_rpc.success(request, self._initialize())
        if method == "tools/list":
            return json_rpc.success(request, self._list_tools())
        if method == "tools/call":
            return await self._call_tool(request)
        if method == "ping":
            return json_rpc.success(request, {})
        return json_rpc.error(
            request,
            BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND,
            f"MCP method '{method}' is not supported by NWP Bridge server.",
        )

    def _initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": McpServerProtocol.VERSION,
            "serverInfo": {
                "name": self._options.server_name,
                "version": self._options.server_version,
            },
            "capabilities": {"tools": {"listChanged": False}},
        }

    def _list_tools(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "name": action.effective_tool_name,
                    "description": action.description,
                    "inputSchema": action.input_schema
                    if action.input_schema is not None
                    else _default_input_schema(),
                }
                for action in self._options.actions
            ]
        }

    async def _call_tool(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        params = request.params
        if not isinstance(params, dict):
            return json_rpc.error(
                request, BridgeJsonRpcErrorCodes.INVALID_PARAMS, "MCP tools/call requires params."
            )

        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return json_rpc.error(
                request,
                BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                "MCP tools/call params.name is required.",
            )

        action = self._resolve_action(name)
        if action is None:
            return json_rpc.error(
                request,
                BridgeJsonRpcErrorCodes.TOOL_NOT_FOUND,
                f"MCP tool '{name}' is not exposed by NWP Bridge server.",
                data={"error": BridgeErrorCodes.SERVER_TOOL_NOT_FOUND, "tool": name},
            )

        frame = ActionFrame(action_id=action.action_id, params=params.get("arguments"), async_=action.async_)

        try:
            result = await self._invoker.invoke(frame)
            return json_rpc.success(request, _to_tool_result(result))
        except Exception as exc:  # noqa: BLE001 - report as tool error payload
            return json_rpc.success(
                request,
                _to_tool_result(
                    ErrorFrame(
                        status="NPS-SERVER-ERROR",
                        error=BridgeErrorCodes.SERVER_DISPATCH_FAILED,
                        message=str(exc),
                    )
                ),
            )

    def _resolve_action(self, tool_name: str) -> BridgeServerAction | None:
        lowered = tool_name.lower()
        for action in self._options.actions:
            if action.effective_tool_name.lower() == lowered or action.action_id.lower() == lowered:
                return action
        return None


def _to_tool_result(frame: Any) -> dict[str, Any]:
    is_error = isinstance(frame, ErrorFrame)
    return {
        "content": [{"type": "text", "text": frame_json.serialize(frame)}],
        "isError": is_error,
    }


def _default_input_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}
