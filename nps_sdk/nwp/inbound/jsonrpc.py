# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""JSON-RPC 2.0 envelopes shared by the inbound MCP and A2A Bridge servers."""

from __future__ import annotations

import dataclasses
from typing import Any

__all__ = [
    "BridgeJsonRpcRequest",
    "BridgeJsonRpcResponse",
    "BridgeJsonRpcError",
    "jsonrpc_success",
    "jsonrpc_error",
]


@dataclasses.dataclass(frozen=True)
class BridgeJsonRpcRequest:
    """A JSON-RPC 2.0 request. ``id`` of ``None`` indicates a notification."""

    method: str
    id: Any = None
    params: Any = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeJsonRpcRequest":
        method = data.get("method")
        if not isinstance(method, str):
            raise ValueError("JSON-RPC request requires a string 'method'.")
        return cls(
            method=method,
            id=data.get("id"),
            params=data.get("params"),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )


@dataclasses.dataclass(frozen=True)
class BridgeJsonRpcError:
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


def jsonrpc_success(request: BridgeJsonRpcRequest, result: Any) -> BridgeJsonRpcResponse:
    return BridgeJsonRpcResponse(id=request.id, result=result)


def jsonrpc_error(
    request: BridgeJsonRpcRequest | None,
    code: int,
    message: str,
    data: Any = None,
) -> BridgeJsonRpcResponse:
    return BridgeJsonRpcResponse(
        id=request.id if request is not None else None,
        error=BridgeJsonRpcError(code=code, message=message, data=data),
    )
