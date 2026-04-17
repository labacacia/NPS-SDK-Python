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
        return cls(
            field=data["field"],
            vector=tuple(float(v) for v in data["vector"]),
            top_k=int(data.get("top_k", 10)),
            threshold=data.get("threshold"),
            metric=data.get("metric", "cosine"),
        )


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
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryFrame":
        order = None
        if data.get("order"):
            order = tuple(QueryOrderClause.from_dict(o) for o in data["order"])

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
