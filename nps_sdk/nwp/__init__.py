# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS NWP — Neural Web Protocol frames and async client."""

from nps_sdk.nwp.frames import (
    NWP_TOPOLOGY_SNAPSHOT,
    NWP_TOPOLOGY_STREAM,
    BridgeNodeSpec,
    QueryOrderClause,
    ActionFrame,
    AsyncActionResponse,
    QueryFrame,
    TopologyEvent,
    TopologyMember,
    TopologySnapshotRequest,
    TopologyStreamRequest,
    VectorSearchOptions,
)
from nps_sdk.nwp.client import NwpClient
from nps_sdk.nwp import error_codes
from nps_sdk.nwp.error_codes import NWP_ERROR_TO_NPS_STATUS
from nps_sdk.nwp.anchor_client import (
    MemberInfo,
    TopologySnapshot,
    TopologyFilter,
    MemberChanges,
    TopologyEvent,
    MemberJoined,
    MemberLeft,
    MemberUpdated,
    AnchorState,
    ResyncRequired,
    AnchorTopologyException,
    AnchorNodeClient,
)
from nps_sdk.nwp.cgn import (
    estimate_cgn,
    estimate_cgn_json,
    estimate_cgn_rows,
    TokenBudgetMeta,
    BudgetExceededError,
)
from nps_sdk.nwp.bridge import (
    BridgeProtocols,
    BridgeNodeDescriptor,
    BridgeTarget,
    NODE_TYPE_BRIDGE,
)

__all__ = [
    "NWP_TOPOLOGY_SNAPSHOT",
    "NWP_TOPOLOGY_STREAM",
    "BridgeNodeSpec",
    "QueryOrderClause",
    "VectorSearchOptions",
    "QueryFrame",
    "TopologyEvent",
    "TopologyMember",
    "TopologySnapshotRequest",
    "TopologyStreamRequest",
    "ActionFrame",
    "AsyncActionResponse",
    "NwpClient",
    # error codes
    "error_codes",
    "NWP_ERROR_TO_NPS_STATUS",
    # Anchor topology
    "MemberInfo",
    "TopologySnapshot",
    "TopologyFilter",
    "MemberChanges",
    "TopologyEvent",
    "MemberJoined",
    "MemberLeft",
    "MemberUpdated",
    "AnchorState",
    "ResyncRequired",
    "AnchorTopologyException",
    "AnchorNodeClient",
    # CGN token-budget helpers
    "estimate_cgn",
    "estimate_cgn_json",
    "estimate_cgn_rows",
    "TokenBudgetMeta",
    "BudgetExceededError",
    # Bridge Node types
    "BridgeProtocols",
    "BridgeNodeDescriptor",
    "BridgeTarget",
    "NODE_TYPE_BRIDGE",
]
