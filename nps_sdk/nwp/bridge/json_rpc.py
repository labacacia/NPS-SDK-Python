# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""JSON-RPC 2.0 envelope helpers used by MCP and A2A Bridge servers
(port of .NET ``BridgeJsonRpc`` and ``BridgeJsonRpcErrorCodes``)."""
from __future__ import annotations

import dataclasses
from typing import Any


class BridgeJsonRpcErrorCodes:
    """Standard JSON-RPC error codes plus Bridge server application codes."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    UPSTREAM_ERROR = -32000
    TOOL_NOT_FOUND = -32002


@dataclasses.dataclass(frozen=True)
class BridgeJsonRpcRequest:
    """JSON-RPC 2.0 request envelope."""

    method: str
    id: Any = None
    params: Any = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeJsonRpcRequest":
        if "method" not in data or not isinstance(data["method"], str):
            raise ValueError("JSON-RPC request.method is required.")
        return cls(
            method=data["method"],
            id=data.get("id"),
            params=data.get("params"),
            jsonrpc=str(data.get("jsonrpc", "2.0")),
        )


@dataclasses.dataclass(frozen=True)
class BridgeJsonRpcError:
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


@dataclasses.dataclass(frozen=True)
class BridgeJsonRpcResponse:
    """JSON-RPC 2.0 response envelope."""

    id: Any = None
    result: Any = None
    error: BridgeJsonRpcError | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = self.error.to_dict()
        else:
            d["result"] = self.result
        return d


def success(request: BridgeJsonRpcRequest, result: Any) -> BridgeJsonRpcResponse:
    return BridgeJsonRpcResponse(id=request.id, result=result)


def error(
    request_or_id: BridgeJsonRpcRequest | Any,
    code: int,
    message: str,
    data: Any = None,
) -> BridgeJsonRpcResponse:
    rid = request_or_id.id if isinstance(request_or_id, BridgeJsonRpcRequest) else request_or_id
    return BridgeJsonRpcResponse(id=rid, error=BridgeJsonRpcError(code=code, message=message, data=data))
