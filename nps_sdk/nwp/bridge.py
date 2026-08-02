# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NWP Bridge Node type definitions (NPS-2 §2A, NPS-CR-0001)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


class BridgeProtocols:
    """Standard bridge_protocols wire-string constants (NPS-CR-0001 §3)."""
    HTTP  = "http"
    GRPC  = "grpc"
    MCP   = "mcp"
    A2A   = "a2a"
    STANDARD: tuple[str, ...] = ("http", "grpc", "mcp", "a2a")


NODE_TYPE_BRIDGE = "bridge"


@dataclass(frozen=True)
class BridgeNodeDescriptor:
    """Declares which external protocols a Bridge Node bridges, in each direction.

    supported_protocols -> Announce.bridge_protocols          (OUTBOUND: NPS -> external)
    inbound_protocols   -> Announce.bridge_inbound_protocols  (INBOUND:  external -> NPS)

    An empty ``inbound_protocols`` means the node exposes no inbound surface -- an
    outbound-only Bridge Node, the only kind that existed through alpha.15. A Bridge Node
    MUST have at least one of the two non-empty. (NPS-CR-0010)
    """
    nid:                 str
    supported_protocols: frozenset[str]
    inbound_protocols:   frozenset[str] = frozenset()

    def to_bridge_protocols(self) -> list[str]:
        return sorted(self.supported_protocols)

    def to_bridge_inbound_protocols(self) -> list[str]:
        return sorted(self.inbound_protocols)


@dataclass(frozen=True)
class BridgeTarget:
    """Inbound parameter object surfacing the bridge_target for a bridge invocation.
    Protocol must be one of BridgeProtocols constants or a future-CR value."""
    protocol: str
    endpoint: str
    extras:   dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"protocol": self.protocol, "endpoint": self.endpoint}
        if self.extras is not None:
            d["extras"] = self.extras
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeTarget":
        return cls(
            protocol=data["protocol"],
            endpoint=data["endpoint"],
            extras=data.get("extras"),
        )
