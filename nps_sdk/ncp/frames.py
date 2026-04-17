# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NCP — Neural Communication Protocol frame dataclasses.

Frame hex codes (NPS-1 §4):
  AnchorFrame  0x01 — schema anchor; establishes global schema reference.
  DiffFrame    0x02 — incremental RFC 6902 JSON Patch.
  StreamFrame  0x03 — streaming chunk with back-pressure.
  CapsFrame    0x04 — full response capsule referencing an anchor.
  ErrorFrame   0xFE — unified error frame (all protocol layers).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from nps_sdk.core.codec import NpsFrame
from nps_sdk.core.frames import EncodingTier, FrameType


# ── FrameSchema ───────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SchemaField:
    """Descriptor for a single field within a FrameSchema (NPS-1 §4.1)."""

    name:     str
    type:     str
    semantic: str | None = None
    nullable: bool       = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.semantic is not None:
            d["semantic"] = self.semantic
        if self.nullable:
            d["nullable"] = self.nullable
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchemaField":
        return cls(
            name=data["name"],
            type=data["type"],
            semantic=data.get("semantic"),
            nullable=bool(data.get("nullable", False)),
        )


@dataclasses.dataclass(frozen=True)
class FrameSchema:
    """Schema definition carried inside an AnchorFrame (NPS-1 §4.1)."""

    fields: tuple[SchemaField, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [f.to_dict() for f in self.fields]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrameSchema":
        return cls(
            fields=tuple(SchemaField.from_dict(f) for f in data.get("fields", []))
        )


# ── AnchorFrame (0x01) ───────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class AnchorFrame(NpsFrame):
    """
    Schema anchor frame (NPS-1 §4.1).
    Carries the full schema definition on first contact; subsequent messages
    reference it by anchor_id only.
    Typical token saving: 30–60 % per session.
    """

    anchor_id: str
    schema:    FrameSchema
    ttl:       int = 3600

    @property
    def frame_type(self) -> FrameType:
        return FrameType.ANCHOR

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "schema":    self.schema.to_dict(),
            "ttl":       self.ttl,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnchorFrame":
        return cls(
            anchor_id=data["anchor_id"],
            schema=FrameSchema.from_dict(data["schema"]),
            ttl=int(data.get("ttl", 3600)),
        )


# ── DiffFrame (0x02) ─────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class JsonPatchOperation:
    """A single RFC 6902 JSON Patch operation (NPS-1 §4.2)."""

    op:    str
    path:  str
    value: Any         = None
    from_: str | None  = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.value is not None:
            d["value"] = self.value
        if self.from_ is not None:
            d["from"] = self.from_
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonPatchOperation":
        return cls(
            op=data["op"],
            path=data["path"],
            value=data.get("value"),
            from_=data.get("from"),
        )


@dataclasses.dataclass(frozen=True)
class DiffFrame(NpsFrame):
    """
    Incremental diff frame (NPS-1 §4.2).
    Carries only changed fields as RFC 6902 JSON Patch operations,
    referencing the base schema via anchor_ref.
    """

    anchor_ref: str
    base_seq:   int
    patch:      tuple[JsonPatchOperation, ...]
    entity_id:  str | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.DIFF

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "anchor_ref": self.anchor_ref,
            "base_seq":   self.base_seq,
            "patch":      [op.to_dict() for op in self.patch],
        }
        if self.entity_id is not None:
            d["entity_id"] = self.entity_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiffFrame":
        return cls(
            anchor_ref=data["anchor_ref"],
            base_seq=int(data["base_seq"]),
            patch=tuple(JsonPatchOperation.from_dict(op) for op in data.get("patch", [])),
            entity_id=data.get("entity_id"),
        )


# ── StreamFrame (0x03) ───────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class StreamFrame(NpsFrame):
    """
    Streaming chunk frame (NPS-1 §4.3).
    Chunks are ordered by seq and reassembled by the receiver.
    Back-pressure is signalled via window_size.
    """

    stream_id:   str
    seq:         int
    is_last:     bool
    data:        tuple[Any, ...]
    anchor_ref:  str | None = None
    window_size: int | None = None
    error_code:  str | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.STREAM

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stream_id": self.stream_id,
            "seq":       self.seq,
            "is_last":   self.is_last,
            "data":      list(self.data),
        }
        if self.anchor_ref  is not None: d["anchor_ref"]  = self.anchor_ref
        if self.window_size is not None: d["window_size"] = self.window_size
        if self.error_code  is not None: d["error_code"]  = self.error_code
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StreamFrame":
        return cls(
            stream_id=data["stream_id"],
            seq=int(data["seq"]),
            is_last=bool(data["is_last"]),
            data=tuple(data.get("data", [])),
            anchor_ref=data.get("anchor_ref"),
            window_size=data.get("window_size"),
            error_code=data.get("error_code"),
        )


# ── CapsFrame (0x04) ─────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class CapsFrame(NpsFrame):
    """
    Capsule frame — full response envelope (NPS-1 §4.4).
    References a cached anchor schema and carries the complete result set,
    with optional cursor for pagination.
    """

    anchor_ref:     str
    count:          int
    data:           tuple[Any, ...]
    next_cursor:    str | None = None
    token_est:      int | None = None
    cached:         bool | None = None
    tokenizer_used: str | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.CAPS

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "anchor_ref": self.anchor_ref,
            "count":      self.count,
            "data":       list(self.data),
        }
        if self.next_cursor    is not None: d["next_cursor"]    = self.next_cursor
        if self.token_est      is not None: d["token_est"]      = self.token_est
        if self.cached         is not None: d["cached"]         = self.cached
        if self.tokenizer_used is not None: d["tokenizer_used"] = self.tokenizer_used
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapsFrame":
        return cls(
            anchor_ref=data["anchor_ref"],
            count=int(data["count"]),
            data=tuple(data.get("data", [])),
            next_cursor=data.get("next_cursor"),
            token_est=data.get("token_est"),
            cached=data.get("cached"),
            tokenizer_used=data.get("tokenizer_used"),
        )


# ── ErrorFrame (0xFE) ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ErrorFrame(NpsFrame):
    """
    Unified error frame shared across all NPS protocol layers (NPS-1 §4.6).
    In native transport mode, errors are conveyed via this frame type.
    """

    status:  str
    error:   str
    message: str | None = None
    details: Any        = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.ERROR

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": self.status, "error": self.error}
        if self.message is not None: d["message"] = self.message
        if self.details is not None: d["details"] = self.details
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ErrorFrame":
        return cls(
            status=data["status"],
            error=data["error"],
            message=data.get("message"),
            details=data.get("details"),
        )
