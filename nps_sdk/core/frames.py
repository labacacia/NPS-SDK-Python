# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS wire-level frame primitives: FrameType, FrameFlags, EncodingTier, FrameHeader."""

from __future__ import annotations

import struct
from enum import IntEnum, IntFlag

from nps_sdk.core.exceptions import NpsFrameError


class FrameType(IntEnum):
    """Unified frame byte namespace for the full NPS suite (NPS-0 §9)."""

    # NCP  0x01–0x0F
    ANCHOR       = 0x01
    DIFF         = 0x02
    STREAM       = 0x03
    CAPS         = 0x04
    ALIGN        = 0x05  # deprecated — use AlignStream (0x43)

    # NWP  0x10–0x1F
    QUERY        = 0x10
    ACTION       = 0x11

    # NIP  0x20–0x2F
    IDENT        = 0x20
    TRUST        = 0x21
    REVOKE       = 0x22

    # NDP  0x30–0x3F
    ANNOUNCE     = 0x30
    RESOLVE      = 0x31
    GRAPH        = 0x32

    # NOP  0x40–0x4F
    TASK         = 0x40
    DELEGATE     = 0x41
    SYNC         = 0x42
    ALIGN_STREAM = 0x43

    # Reserved / System  0xF0–0xFF
    ERROR        = 0xFE


class EncodingTier(IntEnum):
    """
    Wire encoding tier, stored in the lower 2 bits of the flags byte (NPS-1 §3.2).

    0x00 = Tier-1 JSON   — human-readable; development / compatibility.
    0x01 = Tier-2 MsgPack — binary, ~60 % smaller than JSON; production default.
    0x02, 0x03 = Reserved.
    """

    JSON     = 0x00
    MSGPACK  = 0x01


class FrameFlags(IntFlag):
    """
    Flags byte in the 4-byte fixed frame header (NPS-1 §3.2).

    Bit layout (LSB = bit 0):
      Bits 0–1 (T0, T1) : Encoding tier (EncodingTier).
      Bit 2  (FINAL)    : Last chunk of a StreamFrame; non-stream frames MUST set this.
      Bit 3  (ENC)      : Payload encrypted; MUST be 1 in production.
      Bits 4–6 (RSV)    : Reserved — sender MUST write 0, receiver MUST ignore.
      Bit 7  (EXT)      : Extended 8-byte header (4-byte payload length).
    """

    NONE          = 0x00
    TIER1_JSON    = 0x00  # same value as NONE — explicit alias
    TIER2_MSGPACK = 0x01
    FINAL         = 0x04
    ENCRYPTED     = 0x08
    EXT           = 0x80


# ── FrameHeader ──────────────────────────────────────────────────────────────

_HEADER_DEFAULT_FMT  = ">BBH"   # type(1) flags(1) length(2)  — 4 bytes
_HEADER_EXTENDED_FMT = ">BBHI"  # type(1) flags(1) rsv(2) length(4) — 8 bytes

DEFAULT_HEADER_SIZE  = 4
EXTENDED_HEADER_SIZE = 8
DEFAULT_MAX_PAYLOAD  = 0xFFFF        # 65 535 bytes
EXTENDED_MAX_PAYLOAD = 0xFFFF_FFFF   # 4 GiB - 1


class FrameHeader:
    """
    NPS frame header, present at the start of every wire message (NPS-1 §3.1).

    Default (4 bytes, EXT=0):
        Byte 0   : FrameType
        Byte 1   : Flags
        Byte 2–3 : PayloadLength (big-endian uint16)

    Extended (8 bytes, EXT=1):
        Byte 0   : FrameType
        Byte 1   : Flags (bit 7 = 1)
        Byte 2–3 : Reserved (must be 0)
        Byte 4–7 : PayloadLength (big-endian uint32)
    """

    __slots__ = ("frame_type", "flags", "payload_length")

    def __init__(
        self,
        frame_type: FrameType,
        flags: FrameFlags,
        payload_length: int,
    ) -> None:
        self.frame_type     = frame_type
        self.flags          = flags
        self.payload_length = payload_length

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def is_extended(self) -> bool:
        return bool(self.flags & FrameFlags.EXT)

    @property
    def header_size(self) -> int:
        return EXTENDED_HEADER_SIZE if self.is_extended else DEFAULT_HEADER_SIZE

    @property
    def encoding_tier(self) -> EncodingTier:
        return EncodingTier(int(self.flags) & 0x03)

    @property
    def is_final(self) -> bool:
        return bool(int(self.flags) & 0x04)

    @property
    def is_encrypted(self) -> bool:
        return bool(int(self.flags) & 0x08)

    # ── Parsing ───────────────────────────────────────────────────────────────

    @classmethod
    def parse(cls, buf: bytes | bytearray | memoryview) -> "FrameHeader":
        """Parse a frame header from the start of *buf*."""
        if len(buf) < 2:
            raise NpsFrameError(
                f"Buffer too small to read frame type and flags: "
                f"need >= 2 bytes, got {len(buf)}."
            )

        flags = FrameFlags(buf[1])
        ext   = bool(flags & FrameFlags.EXT)

        if ext:
            if len(buf) < EXTENDED_HEADER_SIZE:
                raise NpsFrameError(
                    f"Buffer too small for extended frame header: "
                    f"need {EXTENDED_HEADER_SIZE} bytes, got {len(buf)}."
                )
            # Extended: [type(1) | flags(1) | reserved(2) | length(4)]
            _, _, _rsv, payload_length = struct.unpack_from(">BBHI", buf, 0)
            return cls(FrameType(buf[0]), flags, payload_length)

        if len(buf) < DEFAULT_HEADER_SIZE:
            raise NpsFrameError(
                f"Buffer too small for frame header: "
                f"need {DEFAULT_HEADER_SIZE} bytes, got {len(buf)}."
            )
        _, _, payload_length = struct.unpack_from(">BBH", buf, 0)
        return cls(FrameType(buf[0]), flags, payload_length)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        """Serialise this header to bytes (4 or 8 bytes depending on EXT flag)."""
        if self.is_extended:
            # [type(1) | flags(1) | reserved(2) | length(4)]
            return struct.pack(
                ">BBHI",
                int(self.frame_type),
                int(self.flags),
                0,  # reserved
                self.payload_length,
            )
        return struct.pack(
            ">BBH",
            int(self.frame_type),
            int(self.flags),
            self.payload_length,
        )

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"FrameHeader(frame_type={self.frame_type!r}, "
            f"flags={self.flags!r}, payload_length={self.payload_length})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FrameHeader):
            return NotImplemented
        return (
            self.frame_type     == other.frame_type
            and self.flags          == other.flags
            and self.payload_length == other.payload_length
        )
