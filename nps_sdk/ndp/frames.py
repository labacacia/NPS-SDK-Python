# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NDP — Neural Discovery Protocol frame dataclasses.

  AnnounceFrame  0x30 — node/agent presence broadcast.
  ResolveFrame   0x31 — nwp:// address resolution request/response.
  GraphFrame     0x32 — node graph full-sync or incremental update.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from nps_sdk.core.codec import NpsFrame
from nps_sdk.core.frames import EncodingTier, FrameType


# ── NdpAddress ───────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NdpAddress:
    """A single physical endpoint address for a node (NPS-4 §4.1)."""

    host:     str   # hostname or IP
    port:     int
    protocol: str   # "https" | "nps-native"

    def to_dict(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "protocol": self.protocol}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpAddress":
        return cls(host=data["host"], port=int(data["port"]), protocol=data["protocol"])


# ── NdpResolveResult ─────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NdpResolveResult:
    """Resolved physical endpoint returned inside a ResolveFrame response (NPS-4 §5.2)."""

    host:             str
    port:             int
    ttl:              int
    cert_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"host": self.host, "port": self.port, "ttl": self.ttl}
        if self.cert_fingerprint is not None:
            d["cert_fingerprint"] = self.cert_fingerprint
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpResolveResult":
        return cls(
            host=data["host"],
            port=int(data["port"]),
            ttl=int(data["ttl"]),
            cert_fingerprint=data.get("cert_fingerprint"),
        )


# ── NdpGraphNode ─────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NdpGraphNode:
    """A node entry in a topology GraphFrame (NPS-4 §5)."""
    nid:             str
    cluster_anchor:  str | None = None
    node_roles:      tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"nid": self.nid}
        if self.cluster_anchor is not None:
            d["cluster_anchor"] = self.cluster_anchor
        if self.node_roles:
            d["node_roles"] = list(self.node_roles)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpGraphNode":
        return cls(
            nid=data["nid"],
            cluster_anchor=data.get("cluster_anchor"),
            node_roles=tuple(data.get("node_roles", [])),
        )


# ── NdpGraphEdge ─────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NdpGraphEdge:
    """A directed edge in a topology GraphFrame (NPS-4 §5)."""
    from_nid:    str
    to_nid:      str
    latency_ms:  int | None = None
    protocol:    str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"from_nid": self.from_nid, "to_nid": self.to_nid}
        if self.latency_ms is not None: d["latency_ms"] = self.latency_ms
        if self.protocol is not None: d["protocol"] = self.protocol
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpGraphEdge":
        return cls(
            from_nid=data["from_nid"], to_nid=data["to_nid"],
            latency_ms=data.get("latency_ms"), protocol=data.get("protocol"),
        )


# ── AnnounceFrame (0x30) ─────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class AnnounceFrame(NpsFrame):
    """
    Node/Agent presence broadcast frame (NPS-4 §5.1).

    Broadcast by a node at startup and periodically to keep its registry
    entry alive. TTL=0 signals immediate eviction from all registries.
    Signature covers the canonical JSON of all fields except 'signature'.
    """

    nid:          str
    addresses:    tuple[NdpAddress, ...]
    capabilities: tuple[str, ...]
    ttl:          int    # seconds; 0 = immediate eviction
    timestamp:    str    # ISO 8601 UTC
    signature:    str    # ed25519:<base64url> over canonical JSON
    node_type:    str | None = None
    node_roles:           tuple[str, ...] | None = None
    cluster_anchor:       str | None = None
    spawn_spec_ref:       dict[str, Any] | None = None  # NdpSpawnSpecRef (NDP v0.9 §3.1.1)
    bridge_protocols:     tuple[str, ...] | None = None
    activation_mode:      str | None = None
    activation_endpoint:  str | None = None
    heartbeat_interval_ms: int = 60_000  # NDP v0.9 §3.1

    @property
    def frame_type(self) -> FrameType:
        return FrameType.ANNOUNCE

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def unsigned_dict(self) -> dict[str, Any]:
        """Return the dict used as the Ed25519 signing payload (no 'signature' field)."""
        d = self.to_dict()
        d.pop("signature", None)
        return d

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "nid":          self.nid,
            "addresses":    [a.to_dict() for a in self.addresses],
            "capabilities": list(self.capabilities),
            "ttl":          self.ttl,
            "timestamp":    self.timestamp,
            "signature":    self.signature,
        }
        if self.node_type is not None:
            d["node_type"] = self.node_type
        if self.node_roles is not None:
            d["node_roles"] = list(self.node_roles)
        if self.cluster_anchor is not None:
            d["cluster_anchor"] = self.cluster_anchor
        if self.spawn_spec_ref is not None:
            d["spawn_spec_ref"] = self.spawn_spec_ref
        if self.bridge_protocols is not None:
            d["bridge_protocols"] = list(self.bridge_protocols)
        if self.activation_mode is not None:
            d["activation_mode"] = self.activation_mode
        if self.activation_endpoint is not None:
            d["activation_endpoint"] = self.activation_endpoint
        d["heartbeat_interval_ms"] = self.heartbeat_interval_ms
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnnounceFrame":
        # Support legacy node_kind alias (pre-alpha.3)
        node_roles_raw = data.get("node_roles") or data.get("node_kind")
        bridge_protocols_raw = data.get("bridge_protocols")
        return cls(
            nid=data["nid"],
            addresses=tuple(NdpAddress.from_dict(a) for a in data.get("addresses", [])),
            capabilities=tuple(data.get("capabilities", [])),
            ttl=int(data["ttl"]),
            timestamp=data["timestamp"],
            signature=data["signature"],
            node_type=data.get("node_type"),
            node_roles=tuple(node_roles_raw) if node_roles_raw is not None else None,
            cluster_anchor=data.get("cluster_anchor"),
            spawn_spec_ref=data.get("spawn_spec_ref"),
            bridge_protocols=tuple(bridge_protocols_raw) if bridge_protocols_raw is not None else None,
            activation_mode=data.get("activation_mode"),
            activation_endpoint=data.get("activation_endpoint"),
            heartbeat_interval_ms=int(data.get("heartbeat_interval_ms", 60_000)),
        )


# ── ResolveFrame (0x31) ──────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ResolveFrame(NpsFrame):
    """
    nwp:// address resolution frame (NPS-4 §5.2).

    Used as both request (resolved=None) and response (resolved populated).
    """

    target:        str                  # nwp://host/path
    requester_nid: str | None = None
    resolved:      NdpResolveResult | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.RESOLVE

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"target": self.target}
        if self.requester_nid is not None:
            d["requester_nid"] = self.requester_nid
        if self.resolved is not None:
            d["resolved"] = self.resolved.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResolveFrame":
        resolved_raw = data.get("resolved")
        return cls(
            target=data["target"],
            requester_nid=data.get("requester_nid"),
            resolved=NdpResolveResult.from_dict(resolved_raw) if resolved_raw else None,
        )


# ── GraphFrame (0x32) ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class GraphFrame(NpsFrame):
    """Topology graph snapshot frame (NPS-4 §5). Max 256 nodes, 1024 edges."""
    graph_id:  str
    nodes:     tuple[NdpGraphNode, ...]
    edges:     tuple[NdpGraphEdge, ...]
    ttl:       int
    metadata:  dict[str, Any] | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.GRAPH

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "graph_id": self.graph_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "ttl": self.ttl,
        }
        if self.metadata is not None:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphFrame":
        return cls(
            graph_id=data["graph_id"],
            nodes=tuple(NdpGraphNode.from_dict(n) for n in data.get("nodes", [])),
            edges=tuple(NdpGraphEdge.from_dict(e) for e in data.get("edges", [])),
            ttl=int(data["ttl"]),
            metadata=data.get("metadata"),
        )
