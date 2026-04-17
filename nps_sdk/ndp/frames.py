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
    """A single node entry within a GraphFrame (NPS-4 §5.3)."""

    nid:          str
    addresses:    tuple[NdpAddress, ...]
    capabilities: tuple[str, ...]
    node_type:    str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "nid":          self.nid,
            "addresses":    [a.to_dict() for a in self.addresses],
            "capabilities": list(self.capabilities),
        }
        if self.node_type is not None:
            d["node_type"] = self.node_type
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpGraphNode":
        return cls(
            nid=data["nid"],
            addresses=tuple(NdpAddress.from_dict(a) for a in data.get("addresses", [])),
            capabilities=tuple(data.get("capabilities", [])),
            node_type=data.get("node_type"),
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
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnnounceFrame":
        return cls(
            nid=data["nid"],
            addresses=tuple(NdpAddress.from_dict(a) for a in data.get("addresses", [])),
            capabilities=tuple(data.get("capabilities", [])),
            ttl=int(data["ttl"]),
            timestamp=data["timestamp"],
            signature=data["signature"],
            node_type=data.get("node_type"),
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
    """
    Node graph synchronization frame (NPS-4 §5.3).

    initial_sync=True carries a full node list; False carries a JSON Patch delta.
    seq is strictly monotonically increasing per sender.
    """

    seq:          int
    initial_sync: bool
    nodes:        tuple[NdpGraphNode, ...] | None = None   # full sync
    patch:        Any = None                                # JSON Patch list (incremental)

    @property
    def frame_type(self) -> FrameType:
        return FrameType.GRAPH

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "seq":          self.seq,
            "initial_sync": self.initial_sync,
        }
        if self.nodes is not None:
            d["nodes"] = [n.to_dict() for n in self.nodes]
        if self.patch is not None:
            d["patch"] = self.patch
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphFrame":
        nodes_raw = data.get("nodes")
        return cls(
            seq=int(data["seq"]),
            initial_sync=bool(data["initial_sync"]),
            nodes=tuple(NdpGraphNode.from_dict(n) for n in nodes_raw) if nodes_raw else None,
            patch=data.get("patch"),
        )
