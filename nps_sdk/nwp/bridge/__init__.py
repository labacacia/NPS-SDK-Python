# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NWP Bridge Node subsystem (NPS-2 §2A, NPS-CR-0001).

Stateless translator between NPS frames and non-NPS protocols. Outbound
dispatchers (NPS → external) cover HTTP/HTTPS, gRPC JSON, MCP JSON-RPC, and
A2A JSON-RPC — all JSON / JSON-RPC over HTTP via httpx. Inbound server adapters
(external → NPS) expose local NPS actions to MCP and A2A clients.
"""
from __future__ import annotations

from nps_sdk.nwp.bridge.types import (
    BridgeProtocols,
    BridgeNodeDescriptor,
    BridgeTarget,
    NODE_TYPE_BRIDGE,
)
from nps_sdk.nwp.bridge.errors import (
    BridgeErrorCodes,
    BridgeDispatchException,
)
from nps_sdk.nwp.bridge import target_parser
from nps_sdk.nwp.bridge import endpoint_validator
from nps_sdk.nwp.bridge import json_rpc
from nps_sdk.nwp.bridge import frame_json
from nps_sdk.nwp.bridge.json_rpc import (
    BridgeJsonRpcRequest,
    BridgeJsonRpcResponse,
    BridgeJsonRpcError,
    BridgeJsonRpcErrorCodes,
)
from nps_sdk.nwp.bridge.dispatchers import (
    IBridgeDispatcher,
    HttpBridgeDispatcher,
    GrpcBridgeDispatcher,
    JsonRpcBridgeDispatcher,
    McpBridgeDispatcher,
    A2aBridgeDispatcher,
    BridgeDispatcherRegistry,
    BridgeNode,
)
from nps_sdk.nwp.bridge.server_types import (
    McpServerProtocol,
    A2aServerProtocol,
    A2aTaskState,
)
from nps_sdk.nwp.bridge.server_options import (
    BridgeServerAction,
    BridgeServerOptions,
    IBridgeServerActionInvoker,
    BridgeServerActionInvoker,
    to_tool_name,
)
from nps_sdk.nwp.bridge.mcp_server import McpServerBridge
from nps_sdk.nwp.bridge.a2a_server import A2aServerBridge
from nps_sdk.nwp.bridge.node_middleware import (
    BridgeNodeMiddleware,
    BridgeNodeOptions,
)
from nps_sdk.nwp.bridge.server_middleware import BridgeServerMiddleware

__all__ = [
    # Types
    "BridgeProtocols",
    "BridgeNodeDescriptor",
    "BridgeTarget",
    "NODE_TYPE_BRIDGE",
    # Errors
    "BridgeErrorCodes",
    "BridgeDispatchException",
    # Support modules
    "target_parser",
    "endpoint_validator",
    "json_rpc",
    "frame_json",
    "BridgeJsonRpcRequest",
    "BridgeJsonRpcResponse",
    "BridgeJsonRpcError",
    "BridgeJsonRpcErrorCodes",
    # Outbound dispatchers
    "IBridgeDispatcher",
    "HttpBridgeDispatcher",
    "GrpcBridgeDispatcher",
    "JsonRpcBridgeDispatcher",
    "McpBridgeDispatcher",
    "A2aBridgeDispatcher",
    "BridgeDispatcherRegistry",
    "BridgeNode",
    # Inbound server
    "McpServerProtocol",
    "A2aServerProtocol",
    "A2aTaskState",
    "BridgeServerAction",
    "BridgeServerOptions",
    "IBridgeServerActionInvoker",
    "BridgeServerActionInvoker",
    "to_tool_name",
    "McpServerBridge",
    "A2aServerBridge",
    # Middleware
    "BridgeNodeMiddleware",
    "BridgeNodeOptions",
    "BridgeServerMiddleware",
]
