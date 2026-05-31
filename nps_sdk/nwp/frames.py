# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NWP — Neural Web Protocol frame dataclasses.

  QueryFrame   0x10 — structured data query targeting a Memory Node.
  ActionFrame  0x11 — operation invocation targeting an Action or Complex Node.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from nps_sdk.core.codec import NpsFrame
from nps_sdk.core.frames import EncodingTier, FrameType

NWP_TOPOLOGY_SNAPSHOT = "topology.snapshot"
NWP_TOPOLOGY_STREAM = "topology.stream"


# ── QueryFrame helpers ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class QueryOrderClause:
    """A single ordering rule within a QueryFrame (NPS-2 §5.3)."""

    field: str
    dir:   str  # "ASC" | "DESC"

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "dir": self.dir}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryOrderClause":
        return cls(field=data["field"], dir=data["dir"])


@dataclasses.dataclass(frozen=True)
class VectorSearchOptions:
    """Vector similarity search parameters within a QueryFrame (NPS-2 §5.4)."""

    field:     str
    vector:    tuple[float, ...]
    top_k:     int    = 10
    threshold: float | None = None
    metric:    str    = "cosine"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "field":  self.field,
            "vector": list(self.vector),
            "top_k":  self.top_k,
            "metric": self.metric,
        }
        if self.threshold is not None:
            d["threshold"] = self.threshold
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VectorSearchOptions":
        field = data.get("field", data.get("vectorField", ""))
        return cls(
            field=field,
            vector=tuple(float(v) for v in data["vector"]),
            top_k=int(data.get("top_k", data.get("topK", 10))),
            threshold=data.get("threshold", data.get("minScore")),
            metric=data.get("metric", "cosine"),
        )


@dataclasses.dataclass(frozen=True)
class TopologySnapshotRequest:
    kind: str = NWP_TOPOLOGY_SNAPSHOT
    anchor_ref: str | None = None
    include_bridges: bool = False
    include_capabilities: bool = False
    max_depth: int | None = None
    since: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "include_bridges": self.include_bridges,
            "include_capabilities": self.include_capabilities,
        }
        if self.anchor_ref is not None: d["anchor_ref"] = self.anchor_ref
        if self.max_depth is not None: d["max_depth"] = self.max_depth
        if self.since is not None: d["since"] = self.since
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TopologySnapshotRequest":
        return cls(
            kind=data.get("kind", NWP_TOPOLOGY_SNAPSHOT),
            anchor_ref=data.get("anchor_ref"),
            include_bridges=bool(data.get("include_bridges", False)),
            include_capabilities=bool(data.get("include_capabilities", False)),
            max_depth=data.get("max_depth"),
            since=data.get("since"),
        )


@dataclasses.dataclass(frozen=True)
class TopologyStreamRequest:
    kind: str = NWP_TOPOLOGY_STREAM
    anchor_ref: str | None = None
    include_initial_snapshot: bool = True
    event_types: tuple[str, ...] | None = None
    since: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "include_initial_snapshot": self.include_initial_snapshot,
        }
        if self.anchor_ref is not None: d["anchor_ref"] = self.anchor_ref
        if self.event_types is not None: d["event_types"] = list(self.event_types)
        if self.since is not None: d["since"] = self.since
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TopologyStreamRequest":
        event_types = None
        if data.get("event_types") is not None:
            event_types = tuple(data["event_types"])
        return cls(
            kind=data.get("kind", NWP_TOPOLOGY_STREAM),
            anchor_ref=data.get("anchor_ref"),
            include_initial_snapshot=bool(data.get("include_initial_snapshot", True)),
            event_types=event_types,
            since=data.get("since"),
        )


@dataclasses.dataclass(frozen=True)
class TopologyMember:
    node_id: str
    node_type: str | None = None
    anchor_ref: str | None = None
    capabilities: tuple[str, ...] | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"node_id": self.node_id}
        if self.node_type is not None: d["node_type"] = self.node_type
        if self.anchor_ref is not None: d["anchor_ref"] = self.anchor_ref
        if self.capabilities is not None: d["capabilities"] = list(self.capabilities)
        if self.metadata is not None: d["metadata"] = self.metadata
        return d


@dataclasses.dataclass(frozen=True)
class TopologyEvent:
    event_id: str
    event_type: str
    node_id: str | None = None
    anchor_ref: str | None = None
    timestamp: str | None = None
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"event_id": self.event_id, "event_type": self.event_type}
        if self.node_id is not None: d["node_id"] = self.node_id
        if self.anchor_ref is not None: d["anchor_ref"] = self.anchor_ref
        if self.timestamp is not None: d["timestamp"] = self.timestamp
        if self.payload is not None: d["payload"] = self.payload
        return d


@dataclasses.dataclass(frozen=True)
class BridgeNodeSpec:
    bridge_id: str
    source_protocol: str
    target_protocol: str
    source_ref: str | None = None
    target_ref: str | None = None
    capabilities: tuple[str, ...] | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "bridge_id": self.bridge_id,
            "source_protocol": self.source_protocol,
            "target_protocol": self.target_protocol,
        }
        if self.source_ref is not None: d["source_ref"] = self.source_ref
        if self.target_ref is not None: d["target_ref"] = self.target_ref
        if self.capabilities is not None: d["capabilities"] = list(self.capabilities)
        if self.metadata is not None: d["metadata"] = self.metadata
        return d


# ── QueryFrame (0x10) ─────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class QueryFrame(NpsFrame):
    """
    Structured data query frame, targeting a Memory Node (NPS-2 §5).
    Sent to the /query or /stream sub-path of a nwp:// address.
    """

    anchor_ref:    str | None                = None
    filter:        Any                       = None
    fields:        tuple[str, ...] | None    = None
    limit:         int                       = 20
    cursor:        str | None                = None
    order:         tuple[QueryOrderClause, ...] | None = None
    vector_search: VectorSearchOptions | None = None
    depth:         int | None                = None
    auto_anchor:   bool | None               = None
    stream:        bool | None               = None
    aggregate:     dict[str, Any] | None     = None
    token_budget:  int | None                = None
    tokenizer:     str | None                = None
    request_id:    str | None                = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.QUERY

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"limit": self.limit}
        if self.anchor_ref    is not None: d["anchor_ref"]    = self.anchor_ref
        if self.filter        is not None: d["filter"]        = self.filter
        if self.fields        is not None: d["fields"]        = list(self.fields)
        if self.cursor        is not None: d["cursor"]        = self.cursor
        if self.order         is not None: d["order"]         = [o.to_dict() for o in self.order]
        if self.vector_search is not None: d["vector_search"] = self.vector_search.to_dict()
        if self.depth         is not None: d["depth"]         = self.depth
        if self.auto_anchor   is not None: d["auto_anchor"]   = self.auto_anchor
        if self.stream        is not None: d["stream"]        = self.stream
        if self.aggregate     is not None: d["aggregate"]     = self.aggregate
        if self.token_budget  is not None: d["token_budget"]  = self.token_budget
        if self.tokenizer     is not None: d["tokenizer"]     = self.tokenizer
        if self.request_id    is not None: d["request_id"]    = self.request_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryFrame":
        order = None
        order_data = data.get("order", data.get("order_by"))
        if order_data:
            order = tuple(QueryOrderClause.from_dict(o) for o in order_data)

        vs = None
        if data.get("vector_search"):
            vs = VectorSearchOptions.from_dict(data["vector_search"])

        fields = None
        if data.get("fields") is not None:
            fields = tuple(data["fields"])

        return cls(
            anchor_ref=data.get("anchor_ref"),
            filter=data.get("filter"),
            fields=fields,
            limit=int(data.get("limit", 20)),
            cursor=data.get("cursor"),
            order=order,
            vector_search=vs,
            depth=data.get("depth"),
            auto_anchor=data.get("auto_anchor"),
            stream=data.get("stream"),
            aggregate=data.get("aggregate"),
            token_budget=data.get("token_budget"),
            tokenizer=data.get("tokenizer"),
            request_id=data.get("request_id"),
        )


# ── ActionFrame (0x11) ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ActionFrame(NpsFrame):
    """
    Operation invocation frame, targeting an Action or Complex Node (NPS-2 §6).
    Sent to the /invoke sub-path of a nwp:// address.
    """

    action_id:       str
    params:          Any         = None
    idempotency_key: str | None  = None
    timeout_ms:      int         = 5000
    async_:          bool        = False

    @property
    def frame_type(self) -> FrameType:
        return FrameType.ACTION

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "action_id":  self.action_id,
            "timeout_ms": self.timeout_ms,
            "async":      self.async_,
        }
        if self.params          is not None: d["params"]          = self.params
        if self.idempotency_key is not None: d["idempotency_key"] = self.idempotency_key
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionFrame":
        return cls(
            action_id=data["action_id"],
            params=data.get("params"),
            idempotency_key=data.get("idempotency_key"),
            timeout_ms=int(data.get("timeout_ms", 5000)),
            async_=bool(data.get("async", False)),
        )


# ── AsyncActionResponse ───────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class AsyncActionResponse:
    """
    Response body for an asynchronous ActionFrame execution (NPS-2 §6.2).
    Returned when ActionFrame.async_ is True.
    """

    task_id:  str
    status:   str
    poll_url: str

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "status": self.status, "poll_url": self.poll_url}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AsyncActionResponse":
        return cls(
            task_id=data["task_id"],
            status=data["status"],
            poll_url=data["poll_url"],
        )


# ── SubscribeFrame (0x12) ─────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SubscribeFrame(NpsFrame):
    """Event subscription frame (NWP v0.13, alpha.11)."""

    subscription_id:       str
    filter:                dict[str, Any] | None = None
    heartbeat_interval_ms: int | None = None
    max_events:            int | None = None
    cursor:                str | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.SUBSCRIBE

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"subscription_id": self.subscription_id}
        if self.filter                is not None: d["filter"]                = self.filter
        if self.heartbeat_interval_ms is not None: d["heartbeat_interval_ms"] = self.heartbeat_interval_ms
        if self.max_events            is not None: d["max_events"]            = self.max_events
        if self.cursor                is not None: d["cursor"]                = self.cursor
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubscribeFrame":
        return cls(
            subscription_id=data["subscription_id"],
            filter=data.get("filter"),
            heartbeat_interval_ms=data.get("heartbeat_interval_ms"),
            max_events=data.get("max_events"),
            cursor=data.get("cursor"),
        )
