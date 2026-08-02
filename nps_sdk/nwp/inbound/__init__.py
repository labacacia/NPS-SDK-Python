# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS-CR-0010 — a Bridge Node's **inbound** surface: foreign protocol → NPS.

A plain MCP / A2A / gRPC client, with no NPS knowledge, reaches the NWP nodes this
Bridge fronts::

    foreign client
      → transport        (host binding: HTTP, stdio, gRPC)
      → auth gate        (X-NWP-Agent NID check + verifier)
      → direction gate   (options.serves_inbound(protocol))
      → protocol server  (McpInboundServer / A2aInboundServer / GrpcInboundService)
      → name resolution  (re-encode-and-compare; bare-id fallback)
      → NwpBackend       (in-process delegate, or HTTP to a remote node)
      → NwpResult        (Ok | NpsStatus + NwpError + Message)
      → BridgeErrorMap   (§16.3) → foreign-protocol success or error
"""

from nps_sdk.nwp.inbound.a2a_server import A2A_AGENT_CARD_PATH, A2aInboundServer
from nps_sdk.nwp.inbound.backend import (
    OPEN_OBJECT_SCHEMA,
    HttpNwpBackend,
    InProcessNwpBackend,
    NwpActionDescriptor,
    NwpBackend,
    NwpNodeDescriptor,
    NwpNodeRole,
    NwpResult,
    NwpUpstream,
)
from nps_sdk.nwp.inbound.error_map import (
    BridgeErrorCodes,
    BridgeErrorMap,
    BridgeJsonRpcErrorCodes,
)
from nps_sdk.nwp.inbound.grpc_service import (
    ActionsResponse,
    GrpcInboundService,
    GrpcRpcError,
    InvokeResponse,
    ManifestResponse,
    QueryResponse,
    UpstreamContext,
)
from nps_sdk.nwp.inbound.jsonrpc import (
    BridgeJsonRpcError,
    BridgeJsonRpcRequest,
    BridgeJsonRpcResponse,
    jsonrpc_error,
    jsonrpc_success,
)
from nps_sdk.nwp.inbound.mcp_server import McpInboundServer, McpToolName
from nps_sdk.nwp.inbound.options import (
    BridgeInboundOptions,
    BridgeServerAction,
    create_backends,
)

__all__ = [
    # backends
    "NwpNodeRole",
    "NwpNodeDescriptor",
    "NwpActionDescriptor",
    "NwpResult",
    "NwpBackend",
    "InProcessNwpBackend",
    "NwpUpstream",
    "HttpNwpBackend",
    "OPEN_OBJECT_SCHEMA",
    # options
    "BridgeInboundOptions",
    "BridgeServerAction",
    "create_backends",
    # error mapping (§16.3)
    "BridgeErrorCodes",
    "BridgeErrorMap",
    "BridgeJsonRpcErrorCodes",
    # JSON-RPC envelope
    "BridgeJsonRpcRequest",
    "BridgeJsonRpcResponse",
    "BridgeJsonRpcError",
    "jsonrpc_success",
    "jsonrpc_error",
    # protocol servers
    "McpInboundServer",
    "McpToolName",
    "A2aInboundServer",
    "A2A_AGENT_CARD_PATH",
    "GrpcInboundService",
    "GrpcRpcError",
    "UpstreamContext",
    "ManifestResponse",
    "InvokeResponse",
    "QueryResponse",
    "ActionsResponse",
]
