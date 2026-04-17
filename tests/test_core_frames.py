# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for core frame header parsing and serialisation."""

import struct
import pytest

from nps_sdk.core.frames import (
    EncodingTier,
    FrameFlags,
    FrameHeader,
    FrameType,
    DEFAULT_HEADER_SIZE,
    EXTENDED_HEADER_SIZE,
    DEFAULT_MAX_PAYLOAD,
)
from nps_sdk.core.exceptions import NpsFrameError


class TestFrameHeader:
    def _make_default(
        self,
        frame_type: FrameType = FrameType.ANCHOR,
        flags: FrameFlags = FrameFlags.TIER2_MSGPACK | FrameFlags.FINAL,
        payload_length: int = 42,
    ) -> bytes:
        return struct.pack(">BBH", int(frame_type), int(flags), payload_length)

    def _make_extended(
        self,
        frame_type: FrameType = FrameType.STREAM,
        payload_length: int = 100_000,
    ) -> bytes:
        flags = int(FrameFlags.TIER2_MSGPACK | FrameFlags.EXT)
        return struct.pack(">BBHI", int(frame_type), flags, 0, payload_length)

    # ── parse — default ──────────────────────────────────────────────────────

    def test_parse_default_header(self):
        raw = self._make_default()
        h   = FrameHeader.parse(raw)
        assert h.frame_type     == FrameType.ANCHOR
        assert h.encoding_tier  == EncodingTier.MSGPACK
        assert h.is_final       is True
        assert h.is_extended    is False
        assert h.payload_length == 42
        assert h.header_size    == DEFAULT_HEADER_SIZE

    def test_parse_json_tier(self):
        raw = struct.pack(">BBH", int(FrameType.ERROR), int(FrameFlags.FINAL), 10)
        h   = FrameHeader.parse(raw)
        assert h.encoding_tier == EncodingTier.JSON

    def test_parse_extended_header(self):
        raw = self._make_extended(payload_length=70_000)
        h   = FrameHeader.parse(raw)
        assert h.is_extended    is True
        assert h.payload_length == 70_000
        assert h.header_size    == EXTENDED_HEADER_SIZE
        assert h.frame_type     == FrameType.STREAM

    def test_parse_encrypted_flag(self):
        flags = int(FrameFlags.TIER2_MSGPACK | FrameFlags.FINAL | FrameFlags.ENCRYPTED)
        raw   = struct.pack(">BBH", int(FrameType.CAPS), flags, 5)
        h     = FrameHeader.parse(raw)
        assert h.is_encrypted is True

    def test_parse_too_short_raises(self):
        with pytest.raises(NpsFrameError):
            FrameHeader.parse(b"\x01")

    def test_parse_extended_too_short_raises(self):
        # flags byte has EXT bit set but buffer is only 4 bytes
        buf = struct.pack(">BB", int(FrameType.STREAM), int(FrameFlags.EXT)) + b"\x00\x00"
        with pytest.raises(NpsFrameError):
            FrameHeader.parse(buf)

    # ── round-trip ────────────────────────────────────────────────────────────

    def test_roundtrip_default(self):
        h      = FrameHeader(FrameType.DIFF, FrameFlags.TIER2_MSGPACK | FrameFlags.FINAL, 1234)
        parsed = FrameHeader.parse(h.to_bytes())
        assert parsed == h

    def test_roundtrip_extended(self):
        flags  = FrameFlags.TIER2_MSGPACK | FrameFlags.EXT
        h      = FrameHeader(FrameType.STREAM, flags, 70_000)
        parsed = FrameHeader.parse(h.to_bytes())
        assert parsed == h

    def test_default_header_is_4_bytes(self):
        h = FrameHeader(FrameType.ANCHOR, FrameFlags.FINAL, 0)
        assert len(h.to_bytes()) == 4

    def test_extended_header_is_8_bytes(self):
        h = FrameHeader(FrameType.STREAM, FrameFlags.EXT, 70_000)
        assert len(h.to_bytes()) == 8

    # ── equality & repr ───────────────────────────────────────────────────────

    def test_equality(self):
        a = FrameHeader(FrameType.CAPS, FrameFlags.FINAL, 100)
        b = FrameHeader(FrameType.CAPS, FrameFlags.FINAL, 100)
        assert a == b

    def test_inequality(self):
        a = FrameHeader(FrameType.CAPS, FrameFlags.FINAL, 100)
        b = FrameHeader(FrameType.CAPS, FrameFlags.FINAL, 101)
        assert a != b

    def test_repr(self):
        h = FrameHeader(FrameType.ANCHOR, FrameFlags.FINAL, 0)
        assert "FrameHeader" in repr(h)
