# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS frame codec: Tier-1 (JSON) and Tier-2 (MsgPack) encode/decode,
plus the top-level NpsFrameCodec dispatcher.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

import msgpack

from nps_sdk.core.exceptions import NpsCodecError, NpsFrameError
from nps_sdk.core.frames import (
    DEFAULT_MAX_PAYLOAD,
    EXTENDED_HEADER_SIZE,
    DEFAULT_HEADER_SIZE,
    FrameFlags,
    FrameHeader,
    FrameType,
    EncodingTier,
)

if TYPE_CHECKING:
    from nps_sdk.core.registry import FrameRegistry


# ── Frame protocol ────────────────────────────────────────────────────────────

class NpsFrame:
    """
    Mixin that all NPS frame dataclasses inherit from.
    Provides serialisation helpers used by the codec.
    """

    @property
    def frame_type(self) -> FrameType:  # pragma: no cover
        raise NotImplementedError

    @property
    def preferred_tier(self) -> EncodingTier:  # pragma: no cover
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover
        """Return a plain dict representation (all values JSON-serialisable)."""
        return dataclasses.asdict(self)  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NpsFrame":  # pragma: no cover
        raise NotImplementedError


# ── Tier-1 JSON codec ─────────────────────────────────────────────────────────

class Tier1JsonCodec:
    """
    Tier-1 codec: UTF-8 JSON serialisation.
    Used in development, debugging, and compatibility mode.
    """

    def encode(self, frame: NpsFrame) -> bytes:
        try:
            return json.dumps(frame.to_dict(), separators=(",", ":")).encode("utf-8")
        except Exception as exc:
            raise NpsCodecError(
                f"Tier-1 JSON encode failed for {frame.frame_type!r}."
            ) from exc

    def decode(self, frame_type: FrameType, payload: bytes, registry: "FrameRegistry") -> NpsFrame:
        cls = registry.resolve(frame_type)
        try:
            data = json.loads(payload)
            return cls.from_dict(data)
        except Exception as exc:
            raise NpsCodecError(
                f"Tier-1 JSON decode failed for {frame_type!r}."
            ) from exc


# ── Tier-2 MsgPack codec ──────────────────────────────────────────────────────

class Tier2MsgPackCodec:
    """
    Tier-2 codec: MessagePack binary serialisation.
    Produces ~60 % smaller payloads vs JSON; default for production.
    Uses string keys to remain schema-compatible with the JSON tier.
    """

    def encode(self, frame: NpsFrame) -> bytes:
        try:
            return msgpack.packb(frame.to_dict(), use_bin_type=True)  # type: ignore[call-arg]
        except Exception as exc:
            raise NpsCodecError(
                f"Tier-2 MsgPack encode failed for {frame.frame_type!r}."
            ) from exc

    def decode(self, frame_type: FrameType, payload: bytes, registry: "FrameRegistry") -> NpsFrame:
        cls = registry.resolve(frame_type)
        try:
            data = msgpack.unpackb(payload, raw=False)
            return cls.from_dict(data)
        except Exception as exc:
            raise NpsCodecError(
                f"Tier-2 MsgPack decode failed for {frame_type!r}."
            ) from exc


# ── NpsFrameCodec (dispatcher) ────────────────────────────────────────────────

class NpsFrameCodec:
    """
    Top-level codec dispatcher.
    Reads the EncodingTier from the frame header flags and routes to
    Tier1JsonCodec or Tier2MsgPackCodec accordingly.
    Supports both default (4-byte) and extended (8-byte, EXT=1) header modes
    (NPS-1 §3.1).
    """

    def __init__(
        self,
        registry: "FrameRegistry",
        *,
        max_payload: int = DEFAULT_MAX_PAYLOAD,
    ) -> None:
        self._registry    = registry
        self._max_payload = max_payload
        self._json        = Tier1JsonCodec()
        self._msgpack     = Tier2MsgPackCodec()

    # ── Encode ────────────────────────────────────────────────────────────────

    def encode(
        self,
        frame: NpsFrame,
        *,
        override_tier: EncodingTier | None = None,
    ) -> bytes:
        """
        Serialise *frame* to a complete wire message (header + tier-encoded payload).

        When the payload exceeds 64 KiB but fits within *max_payload*, the EXT flag
        is set automatically and an 8-byte header is emitted.
        """
        from nps_sdk.ncp.frames import StreamFrame  # local import to avoid circulars

        tier  = override_tier if override_tier is not None else frame.preferred_tier
        codec = self._select_codec(tier)

        try:
            payload = codec.encode(frame)
        except NpsCodecError:
            raise
        except Exception as exc:  # pragma: no cover — tier codecs always wrap in NpsCodecError
            raise NpsCodecError(f"Encode failed for {frame.frame_type!r}.") from exc

        if len(payload) > self._max_payload:
            raise NpsCodecError(
                f"Encoded payload for {frame.frame_type!r} exceeds max_frame_payload "
                f"({len(payload)} bytes > {self._max_payload}). "
                "Use StreamFrame (0x03) for large payloads."
            )

        use_ext = len(payload) > DEFAULT_MAX_PAYLOAD
        flags   = self._build_flags(frame, tier)
        if use_ext:
            flags |= FrameFlags.EXT

        header = FrameHeader(frame.frame_type, flags, len(payload))
        return header.to_bytes() + payload

    # ── Decode ────────────────────────────────────────────────────────────────

    def decode(self, wire: bytes) -> NpsFrame:
        """Parse a complete wire message into a typed NpsFrame."""
        header  = FrameHeader.parse(wire)
        payload = wire[header.header_size : header.header_size + header.payload_length]
        codec   = self._select_codec(header.encoding_tier)
        return codec.decode(header.frame_type, payload, self._registry)

    @staticmethod
    def peek_header(wire: bytes) -> FrameHeader:
        """Decode only the header without deserialising the payload. Useful for routing."""
        return FrameHeader.parse(wire)

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_flags(frame: NpsFrame, tier: EncodingTier) -> FrameFlags:
        from nps_sdk.ncp.frames import StreamFrame  # local import to avoid circulars

        flags = FrameFlags.TIER1_JSON if tier == EncodingTier.JSON else FrameFlags.TIER2_MSGPACK

        is_final = not isinstance(frame, StreamFrame) or frame.is_last
        if is_final:
            flags |= FrameFlags.FINAL

        return flags

    def _select_codec(self, tier: EncodingTier) -> Tier1JsonCodec | Tier2MsgPackCodec:
        if tier == EncodingTier.JSON:
            return self._json
        if tier == EncodingTier.MSGPACK:
            return self._msgpack
        raise NpsCodecError(f"Unsupported encoding tier: {tier!r} (0x{int(tier):02X}).")
