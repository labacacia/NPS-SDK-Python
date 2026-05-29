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
    """Declares which external protocols a Bridge Node deployment can reach.
    Used to populate Announce.bridge_protocols."""
    nid:                 str
    supported_protocols: frozenset[str]

    def to_bridge_protocols(self) -> list[str]:
        return sorted(self.supported_protocols)


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
