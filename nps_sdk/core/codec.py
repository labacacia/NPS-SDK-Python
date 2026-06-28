# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS frame codec: Tier-1 (JSON) and Tier-2 (MsgPack) encode/decode,
plus the top-level NpsFrameCodec dispatcher.
"""

from __future__ import annotations

import dataclasses
import json
import math
import struct
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


# ── Tier-3 BinaryVector codec ─────────────────────────────────────────────────

class Tier3BinaryVectorCodec:
    """
    Tier-3 codec: BinaryVector v1.

    The metadata map is MessagePack. Dense vectors under
    ``vector_search.vector`` are replaced by compact markers and carried as
    little-endian float32 vector segments after the metadata.
    """

    MAGIC = b"NPBV"
    VERSION = 1
    PREFIX_SIZE = 16
    MARKER_KEY = "$nps_binary_vector"

    def encode(self, frame: NpsFrame) -> bytes:
        try:
            metadata = frame.to_dict()
            if not isinstance(metadata, dict):
                raise NpsCodecError("Tier-3 BinaryVector metadata root must be an object.")

            vectors: list[tuple[float, ...]] = []
            self._extract_vector_search_vector(metadata, vectors)

            if len(vectors) > 0xFFFF:
                raise NpsCodecError("Tier-3 BinaryVector supports at most 65535 vectors per frame.")

            metadata_bytes = msgpack.packb(metadata, use_bin_type=True)  # type: ignore[call-arg]
            segment_size = sum(4 + len(vector) * 4 for vector in vectors)
            payload = bytearray(self.PREFIX_SIZE + len(metadata_bytes) + segment_size)

            payload[0:4] = self.MAGIC
            payload[4] = self.VERSION
            payload[5] = 0
            struct.pack_into(">H", payload, 6, len(vectors))
            struct.pack_into(">I", payload, 8, len(metadata_bytes))
            struct.pack_into(">I", payload, 12, 0)
            payload[self.PREFIX_SIZE:self.PREFIX_SIZE + len(metadata_bytes)] = metadata_bytes

            offset = self.PREFIX_SIZE + len(metadata_bytes)
            for vector in vectors:
                struct.pack_into(">I", payload, offset, len(vector))
                offset += 4
                for value in vector:
                    struct.pack_into("<f", payload, offset, value)
                    offset += 4

            return bytes(payload)
        except NpsCodecError:
            raise
        except Exception as exc:
            raise NpsCodecError(
                f"Tier-3 BinaryVector encode failed for {frame.frame_type!r}."
            ) from exc

    def decode(self, frame_type: FrameType, payload: bytes, registry: "FrameRegistry") -> NpsFrame:
        cls = registry.resolve(frame_type)
        try:
            metadata, vectors = self._decode_payload(payload)
            self._restore_vector_search_vector(metadata, vectors)
            return cls.from_dict(metadata)
        except NpsCodecError:
            raise
        except Exception as exc:
            raise NpsCodecError(
                f"Tier-3 BinaryVector decode failed for {frame_type!r}."
            ) from exc

    @classmethod
    def _decode_payload(cls, payload: bytes) -> tuple[dict[str, Any], list[tuple[float, ...]]]:
        if len(payload) < cls.PREFIX_SIZE:
            raise NpsCodecError(f"Tier-3 BinaryVector payload too short: {len(payload)} bytes.")

        if payload[0:4] != cls.MAGIC:
            raise NpsCodecError("Tier-3 BinaryVector payload magic mismatch.")

        if payload[4] != cls.VERSION:
            raise NpsCodecError(f"Unsupported Tier-3 BinaryVector version: {payload[4]}.")

        if payload[5] != 0 or struct.unpack_from(">I", payload, 12)[0] != 0:
            raise NpsCodecError("Tier-3 BinaryVector reserved fields must be zero.")

        vector_count = struct.unpack_from(">H", payload, 6)[0]
        metadata_len = struct.unpack_from(">I", payload, 8)[0]
        if metadata_len > len(payload) - cls.PREFIX_SIZE:
            raise NpsCodecError("Tier-3 BinaryVector metadata length exceeds payload length.")

        offset = cls.PREFIX_SIZE
        metadata_bytes = payload[offset:offset + metadata_len]
        offset += metadata_len

        metadata = msgpack.unpackb(metadata_bytes, raw=False)
        if not isinstance(metadata, dict):
            raise NpsCodecError("Tier-3 BinaryVector metadata root must be a map.")

        vectors: list[tuple[float, ...]] = []
        for _ in range(vector_count):
            if len(payload) - offset < 4:
                raise NpsCodecError("Tier-3 BinaryVector vector segment missing dimension.")

            dim = struct.unpack_from(">I", payload, offset)[0]
            offset += 4
            byte_len = dim * 4
            if len(payload) - offset < byte_len:
                raise NpsCodecError("Tier-3 BinaryVector vector segment is truncated.")

            vector = struct.unpack_from(f"<{dim}f", payload, offset) if dim else ()
            offset += byte_len
            vectors.append(tuple(float(v) for v in vector))

        if offset != len(payload):
            raise NpsCodecError("Tier-3 BinaryVector payload has trailing bytes.")

        return metadata, vectors

    @classmethod
    def _extract_vector_search_vector(
        cls,
        metadata: dict[str, Any],
        vectors: list[tuple[float, ...]],
    ) -> None:
        vector_search = metadata.get("vector_search")
        if not isinstance(vector_search, dict):
            return

        vector = vector_search.get("vector")
        if not isinstance(vector, (list, tuple)):
            return

        values: list[float] = []
        for item in vector:
            if not isinstance(item, (int, float)):
                return
            value = float(item)
            if not math.isfinite(value):
                return
            values.append(value)

        index = len(vectors)
        vectors.append(tuple(values))
        vector_search["vector"] = {
            cls.MARKER_KEY: index,
            "dtype": "float32",
            "dim": len(values),
        }

    @classmethod
    def _restore_vector_search_vector(
        cls,
        metadata: dict[str, Any],
        vectors: list[tuple[float, ...]],
    ) -> None:
        vector_search = metadata.get("vector_search")
        if not isinstance(vector_search, dict) or "vector" not in vector_search:
            return

        marker = vector_search["vector"]
        if not isinstance(marker, dict):
            raise NpsCodecError("Tier-3 BinaryVector marker must be an object.")

        if cls.MARKER_KEY not in marker:
            raise NpsCodecError("Tier-3 BinaryVector marker missing vector index.")

        index = int(marker[cls.MARKER_KEY])
        if index < 0 or index >= len(vectors):
            raise NpsCodecError(
                f"Tier-3 BinaryVector marker references vector {index}, "
                f"but only {len(vectors)} vectors are present."
            )

        if marker.get("dtype") != "float32":
            raise NpsCodecError("Tier-3 BinaryVector v1 only supports dtype=float32.")

        dim = int(marker.get("dim"))
        if dim != len(vectors[index]):
            raise NpsCodecError("Tier-3 BinaryVector marker dimension does not match vector segment.")

        vector_search["vector"] = list(vectors[index])


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
        self._json          = Tier1JsonCodec()
        self._msgpack       = Tier2MsgPackCodec()
        self._binary_vector = Tier3BinaryVectorCodec()

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

        if tier == EncodingTier.JSON:
            flags = FrameFlags.TIER1_JSON
        elif tier == EncodingTier.MSGPACK:
            flags = FrameFlags.TIER2_MSGPACK
        elif tier == EncodingTier.BINARY_VECTOR:
            flags = FrameFlags.TIER3_BINARY_VECTOR
        else:
            raise NpsCodecError(f"Unsupported encoding tier: {tier!r} (0x{int(tier):02X}).")

        is_final = not isinstance(frame, StreamFrame) or frame.is_last
        if is_final:
            flags |= FrameFlags.FINAL

        return flags

    def _select_codec(self, tier: EncodingTier) -> Tier1JsonCodec | Tier2MsgPackCodec | Tier3BinaryVectorCodec:
        if tier == EncodingTier.JSON:
            return self._json
        if tier == EncodingTier.MSGPACK:
            return self._msgpack
        if tier == EncodingTier.BINARY_VECTOR:
            return self._binary_vector
        raise NpsCodecError(f"Unsupported encoding tier: {tier!r} (0x{int(tier):02X}).")
