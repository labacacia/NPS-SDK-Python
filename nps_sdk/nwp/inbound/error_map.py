# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
The normative NPS ↔ foreign-protocol error mapping of NWP §16.3 (NPS-CR-0010).

Through alpha.15 this mapping was implemented twice per protocol — once in the
outbound dispatcher, once in the then-separate ``compat/*-ingress`` package — and the
two copies drifted. §16.3 makes the mapping normative and requires a **single**
implementation serving both directions and all three protocols. This is that
implementation; no inbound or outbound path may hand-roll its own.
"""

from __future__ import annotations

from nps_sdk.core.status_codes import (
    NPS_AUTH_FORBIDDEN,
    NPS_AUTH_UNAUTHENTICATED,
    NPS_CLIENT_BAD_FRAME,
    NPS_CLIENT_BAD_PARAM,
    NPS_CLIENT_CONFLICT,
    NPS_CLIENT_GONE,
    NPS_CLIENT_NOT_FOUND,
    NPS_CLIENT_UNPROCESSABLE,
    NPS_DOWNSTREAM_UNAVAILABLE,
    NPS_LIMIT_BUDGET,
    NPS_LIMIT_PAYLOAD,
    NPS_LIMIT_RATE,
    NPS_OK,
    NPS_SERVER_ENCODING_UNSUPPORTED,
    NPS_SERVER_INTERNAL,
    NPS_SERVER_TIMEOUT,
    NPS_SERVER_UNAVAILABLE,
    NPS_SERVER_UNSUPPORTED,
)
from nps_sdk.nwp.error_codes import (
    BRIDGE_DIRECTION_UNSUPPORTED,
    BRIDGE_ENDPOINT_INVALID,
    BRIDGE_PROTOCOL_UNSUPPORTED,
    BRIDGE_SERVER_DISPATCH_FAILED,
    BRIDGE_SERVER_DISPATCHER_MISSING,
    BRIDGE_SERVER_TOOL_NOT_FOUND,
    BRIDGE_TARGET_INVALID,
    BRIDGE_UPSTREAM_FAILED,
)

__all__ = ["BridgeErrorCodes", "BridgeJsonRpcErrorCodes", "BridgeErrorMap"]


class BridgeErrorCodes:
    """NWP error codes used by Bridge dispatchers, in both directions."""

    #: The request targets a protocol/direction pair this Bridge never declared.
    DIRECTION_UNSUPPORTED = BRIDGE_DIRECTION_UNSUPPORTED
    #: Outbound: the invocation carries no valid ``bridge_target``.
    TARGET_INVALID = BRIDGE_TARGET_INVALID
    #: Outbound: the requested bridge protocol has no registered dispatcher.
    PROTOCOL_UNSUPPORTED = BRIDGE_PROTOCOL_UNSUPPORTED
    #: Outbound: the target endpoint is invalid or disallowed.
    ENDPOINT_INVALID = BRIDGE_ENDPOINT_INVALID
    #: Outbound: the external call failed or returned an unusable response.
    UPSTREAM_FAILED = BRIDGE_UPSTREAM_FAILED
    #: Inbound: the foreign client named a tool / action / resource that is not exposed.
    SERVER_TOOL_NOT_FOUND = BRIDGE_SERVER_TOOL_NOT_FOUND
    #: Inbound: no backend was configured for the NPS node this Bridge fronts.
    SERVER_DISPATCHER_MISSING = BRIDGE_SERVER_DISPATCHER_MISSING
    #: Inbound: dispatch to the fronted NPS node failed unexpectedly.
    SERVER_DISPATCH_FAILED = BRIDGE_SERVER_DISPATCH_FAILED


class BridgeJsonRpcErrorCodes:
    """Standard JSON-RPC 2.0 codes plus the Bridge server application codes."""

    # ── Standard JSON-RPC 2.0 ────────────────────────────────────────────────
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # ── Application-defined (JSON-RPC reserves -32000..-32099) ───────────────
    # The NPS status each maps from is normative — see NWP §16.3.

    #: The upstream external service failed or was unreachable.
    UPSTREAM_ERROR = -32000
    #: Maps from ``NPS-AUTH-UNAUTHENTICATED``.
    UNAUTHENTICATED = -32001
    #: Maps from ``NPS-AUTH-FORBIDDEN``.
    FORBIDDEN = -32003
    #: Maps from ``NPS-CLIENT-CONFLICT``.
    CONFLICT = -32004
    #: Maps from ``NPS-LIMIT-RATE`` / ``-BUDGET`` / ``-PAYLOAD``.
    LIMIT_EXCEEDED = -32005

    #: Retired by NPS-CR-0010 and reserved, NOT reused. An unknown tool maps to
    #: ``METHOD_NOT_FOUND`` (-32601), so a client pinned to the alpha.15 behaviour
    #: cannot silently misread a different error as a missing tool. Do not emit.
    RESERVED_TOOL_NOT_FOUND = -32002


_TO_JSON_RPC: dict[str, int] = {
    NPS_CLIENT_BAD_FRAME: BridgeJsonRpcErrorCodes.INVALID_REQUEST,
    NPS_CLIENT_BAD_PARAM: BridgeJsonRpcErrorCodes.INVALID_PARAMS,
    NPS_CLIENT_UNPROCESSABLE: BridgeJsonRpcErrorCodes.INVALID_PARAMS,
    NPS_CLIENT_GONE: BridgeJsonRpcErrorCodes.INVALID_PARAMS,
    NPS_CLIENT_CONFLICT: BridgeJsonRpcErrorCodes.CONFLICT,
    NPS_AUTH_UNAUTHENTICATED: BridgeJsonRpcErrorCodes.UNAUTHENTICATED,
    NPS_AUTH_FORBIDDEN: BridgeJsonRpcErrorCodes.FORBIDDEN,
    NPS_LIMIT_RATE: BridgeJsonRpcErrorCodes.LIMIT_EXCEEDED,
    NPS_LIMIT_BUDGET: BridgeJsonRpcErrorCodes.LIMIT_EXCEEDED,
    NPS_LIMIT_PAYLOAD: BridgeJsonRpcErrorCodes.LIMIT_EXCEEDED,
    # "Not implemented" reads to an MCP client as a method it cannot call.
    NPS_SERVER_UNSUPPORTED: BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND,
}

_TO_GRPC: dict[str, str] = {
    NPS_CLIENT_BAD_FRAME: "INVALID_ARGUMENT",
    NPS_CLIENT_BAD_PARAM: "INVALID_ARGUMENT",
    NPS_CLIENT_UNPROCESSABLE: "INVALID_ARGUMENT",
    NPS_CLIENT_NOT_FOUND: "NOT_FOUND",
    NPS_CLIENT_GONE: "NOT_FOUND",
    NPS_CLIENT_CONFLICT: "ABORTED",
    NPS_AUTH_UNAUTHENTICATED: "UNAUTHENTICATED",
    NPS_AUTH_FORBIDDEN: "PERMISSION_DENIED",
    NPS_LIMIT_RATE: "RESOURCE_EXHAUSTED",
    NPS_LIMIT_BUDGET: "RESOURCE_EXHAUSTED",
    NPS_LIMIT_PAYLOAD: "RESOURCE_EXHAUSTED",
    NPS_SERVER_UNSUPPORTED: "UNIMPLEMENTED",
    NPS_SERVER_INTERNAL: "INTERNAL",
    NPS_SERVER_UNAVAILABLE: "UNAVAILABLE",
    NPS_DOWNSTREAM_UNAVAILABLE: "UNAVAILABLE",
    NPS_SERVER_TIMEOUT: "DEADLINE_EXCEEDED",
}

_FROM_HTTP: dict[int, str] = {
    400: NPS_CLIENT_BAD_PARAM,
    401: NPS_AUTH_UNAUTHENTICATED,
    403: NPS_AUTH_FORBIDDEN,
    404: NPS_CLIENT_NOT_FOUND,
    408: NPS_SERVER_TIMEOUT,
    409: NPS_CLIENT_CONFLICT,
    410: NPS_CLIENT_GONE,
    413: NPS_LIMIT_PAYLOAD,
    415: NPS_SERVER_ENCODING_UNSUPPORTED,
    422: NPS_CLIENT_UNPROCESSABLE,
    429: NPS_LIMIT_RATE,
    501: NPS_SERVER_UNSUPPORTED,
    502: NPS_DOWNSTREAM_UNAVAILABLE,
    503: NPS_SERVER_UNAVAILABLE,
    504: NPS_DOWNSTREAM_UNAVAILABLE,
}

_FROM_JSON_RPC: dict[int, str] = {
    BridgeJsonRpcErrorCodes.PARSE_ERROR: NPS_CLIENT_BAD_FRAME,
    BridgeJsonRpcErrorCodes.INVALID_REQUEST: NPS_CLIENT_BAD_FRAME,
    BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND: NPS_CLIENT_NOT_FOUND,
    BridgeJsonRpcErrorCodes.INVALID_PARAMS: NPS_CLIENT_BAD_PARAM,
    BridgeJsonRpcErrorCodes.INTERNAL_ERROR: NPS_SERVER_INTERNAL,
    BridgeJsonRpcErrorCodes.UNAUTHENTICATED: NPS_AUTH_UNAUTHENTICATED,
    BridgeJsonRpcErrorCodes.FORBIDDEN: NPS_AUTH_FORBIDDEN,
    BridgeJsonRpcErrorCodes.CONFLICT: NPS_CLIENT_CONFLICT,
    BridgeJsonRpcErrorCodes.LIMIT_EXCEEDED: NPS_LIMIT_RATE,
    BridgeJsonRpcErrorCodes.UPSTREAM_ERROR: NPS_DOWNSTREAM_UNAVAILABLE,
}

_FROM_GRPC: dict[str, str] = {
    "OK": NPS_OK,
    "INVALID_ARGUMENT": NPS_CLIENT_BAD_PARAM,
    "FAILED_PRECONDITION": NPS_CLIENT_UNPROCESSABLE,
    "NOT_FOUND": NPS_CLIENT_NOT_FOUND,
    "ALREADY_EXISTS": NPS_CLIENT_CONFLICT,
    "ABORTED": NPS_CLIENT_CONFLICT,
    "UNAUTHENTICATED": NPS_AUTH_UNAUTHENTICATED,
    "PERMISSION_DENIED": NPS_AUTH_FORBIDDEN,
    "RESOURCE_EXHAUSTED": NPS_LIMIT_RATE,
    "UNIMPLEMENTED": NPS_SERVER_UNSUPPORTED,
    "UNAVAILABLE": NPS_SERVER_UNAVAILABLE,
    "DEADLINE_EXCEEDED": NPS_SERVER_TIMEOUT,
    "INTERNAL": NPS_SERVER_INTERNAL,
    "UNKNOWN": NPS_SERVER_INTERNAL,
    "DATA_LOSS": NPS_SERVER_INTERNAL,
}

#: Infrastructure failures — the tool did **not** run. Both pre-CR-0010 inbound
#: implementations returned these as a *successful* result with ``isError: true``,
#: which lets an MCP client mistake a 403 for a tool that merely returned unhappy
#: text. Genuine tool-domain failures (the ``NPS-CLIENT-*`` classes) stay as
#: ``isError: true`` content, which is what MCP's flag is for.
_MUST_BE_PROTOCOL_ERROR: frozenset[str] = frozenset({
    NPS_AUTH_UNAUTHENTICATED,
    NPS_AUTH_FORBIDDEN,
    NPS_LIMIT_RATE,
    NPS_LIMIT_BUDGET,
    NPS_LIMIT_PAYLOAD,
    NPS_SERVER_UNSUPPORTED,
    NPS_SERVER_INTERNAL,
    NPS_SERVER_UNAVAILABLE,
    NPS_SERVER_TIMEOUT,
    NPS_DOWNSTREAM_UNAVAILABLE,
})


class BridgeErrorMap:
    """NWP §16.3 — the single mapping implementation, both directions."""

    @staticmethod
    def to_json_rpc(nps_status: str | None, resource_read: bool = False) -> int:
        """NPS status → JSON-RPC 2.0 error code (MCP, A2A).

        :param resource_read: when True the request was a ``resources/read``, where an
            unknown target is a bad *argument* (-32602) rather than a missing *method*
            (-32601). §16.3 calls this distinction out explicitly and it is the only
            param-sensitive row: an unknown tool is a missing method to an MCP client;
            an unknown resource is not.
        """
        if nps_status == NPS_CLIENT_NOT_FOUND:
            return (BridgeJsonRpcErrorCodes.INVALID_PARAMS if resource_read
                    else BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND)
        return _TO_JSON_RPC.get(nps_status or "", BridgeJsonRpcErrorCodes.INTERNAL_ERROR)

    @staticmethod
    def to_grpc_status(nps_status: str | None) -> str:
        """NPS status → canonical UPPER_SNAKE gRPC status code name."""
        return _TO_GRPC.get(nps_status or "", "INTERNAL")

    @staticmethod
    def from_http_status(http_status: int) -> str:
        """HTTP status → the **most specific** NPS status — never a blanket internal error."""
        mapped = _FROM_HTTP.get(http_status)
        if mapped is not None:
            return mapped
        if http_status >= 500:
            return NPS_SERVER_INTERNAL
        if http_status >= 400:
            return NPS_CLIENT_BAD_PARAM
        return NPS_OK

    @staticmethod
    def from_json_rpc(json_rpc_code: int) -> str:
        """Foreign JSON-RPC error code → NPS status (outbound Bridge direction)."""
        return _FROM_JSON_RPC.get(json_rpc_code, NPS_SERVER_INTERNAL)

    @staticmethod
    def from_grpc_status(grpc_status: str | None) -> str:
        """Foreign gRPC status name → NPS status (outbound Bridge direction)."""
        return _FROM_GRPC.get((grpc_status or "").upper(), NPS_SERVER_INTERNAL)

    @staticmethod
    def must_be_protocol_error(nps_status: str | None) -> bool:
        """Whether a failure MUST surface as a protocol error, not an ``isError`` result."""
        return nps_status in _MUST_BE_PROTOCOL_ERROR
