# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for Tier-1 JSON and Tier-2 MsgPack codecs, and NpsFrameCodec dispatcher."""

import pytest

from nps_sdk.core.codec import NpsFrameCodec, Tier1JsonCodec, Tier2MsgPackCodec
from nps_sdk.core.exceptions import NpsCodecError
from nps_sdk.core.frames import EncodingTier, FrameFlags, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import (
    AnchorFrame,
    CapsFrame,
    DiffFrame,
    ErrorFrame,
    FrameSchema,
    JsonPatchOperation,
    SchemaField,
    StreamFrame,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def schema() -> FrameSchema:
    return FrameSchema(fields=(
        SchemaField(name="id",    type="uint64"),
        SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
        SchemaField(name="label", type="string",  nullable=True),
    ))


@pytest.fixture
def anchor(schema: FrameSchema) -> AnchorFrame:
    return AnchorFrame(anchor_id="sha256:" + "a" * 64, schema=schema, ttl=3600)


@pytest.fixture
def registry() -> FrameRegistry:
    return FrameRegistry.create_default()


@pytest.fixture
def codec(registry: FrameRegistry) -> NpsFrameCodec:
    return NpsFrameCodec(registry)


# ── AnchorFrame round-trips ───────────────────────────────────────────────────

class TestAnchorFrameCodec:
    def test_json_roundtrip(self, anchor: AnchorFrame, registry: FrameRegistry):
        j   = Tier1JsonCodec()
        raw = j.encode(anchor)
        out = j.decode(FrameType.ANCHOR, raw, registry)
        assert isinstance(out, AnchorFrame)
        assert out.anchor_id == anchor.anchor_id
        assert out.ttl       == anchor.ttl
        assert out.schema.fields == anchor.schema.fields

    def test_msgpack_roundtrip(self, anchor: AnchorFrame, registry: FrameRegistry):
        m   = Tier2MsgPackCodec()
        raw = m.encode(anchor)
        out = m.decode(FrameType.ANCHOR, raw, registry)
        assert isinstance(out, AnchorFrame)
        assert out.anchor_id == anchor.anchor_id

    def test_wire_json_roundtrip(self, anchor: AnchorFrame, codec: NpsFrameCodec):
        wire = codec.encode(anchor, override_tier=EncodingTier.JSON)
        out  = codec.decode(wire)
        assert isinstance(out, AnchorFrame)
        assert out.anchor_id == anchor.anchor_id

    def test_wire_msgpack_roundtrip(self, anchor: AnchorFrame, codec: NpsFrameCodec):
        wire = codec.encode(anchor, override_tier=EncodingTier.MSGPACK)
        out  = codec.decode(wire)
        assert isinstance(out, AnchorFrame)
        assert out.anchor_id == anchor.anchor_id


# ── DiffFrame round-trips ─────────────────────────────────────────────────────

class TestDiffFrameCodec:
    def test_roundtrip(self, codec: NpsFrameCodec):
        frame = DiffFrame(
            anchor_ref="sha256:" + "b" * 64,
            base_seq=7,
            patch=(
                JsonPatchOperation(op="replace", path="/price", value=99.9),
                JsonPatchOperation(op="remove",  path="/label"),
            ),
            entity_id="product:42",
        )
        wire = codec.encode(frame)
        out  = codec.decode(wire)
        assert isinstance(out, DiffFrame)
        assert out.anchor_ref == frame.anchor_ref
        assert out.base_seq   == 7
        assert len(out.patch) == 2
        assert out.patch[0].op    == "replace"
        assert out.patch[0].value == 99.9
        assert out.entity_id      == "product:42"

    def test_no_entity_id(self, codec: NpsFrameCodec):
        frame = DiffFrame(
            anchor_ref="sha256:" + "c" * 64,
            base_seq=0,
            patch=(JsonPatchOperation(op="add", path="/x", value=1),),
        )
        wire = codec.encode(frame)
        out  = codec.decode(wire)
        assert out.entity_id is None


# ── StreamFrame round-trips ───────────────────────────────────────────────────

class TestStreamFrameCodec:
    def test_non_final_flag(self, codec: NpsFrameCodec):
        frame = StreamFrame(
            stream_id="s-1",
            seq=0,
            is_last=False,
            data=({"id": 1}, {"id": 2}),
        )
        wire = codec.encode(frame)
        # Non-final stream → FINAL flag should NOT be set
        header = NpsFrameCodec.peek_header(wire)
        assert not header.is_final

    def test_final_flag(self, codec: NpsFrameCodec):
        frame = StreamFrame(
            stream_id="s-1",
            seq=1,
            is_last=True,
            data=({"id": 3},),
        )
        wire   = codec.encode(frame)
        header = NpsFrameCodec.peek_header(wire)
        assert header.is_final

    def test_roundtrip(self, codec: NpsFrameCodec):
        frame = StreamFrame(
            stream_id="s-abc",
            seq=2,
            is_last=True,
            data=({"k": "v"},),
            anchor_ref="sha256:" + "d" * 64,
            window_size=10,
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, StreamFrame)
        assert out.stream_id   == "s-abc"
        assert out.window_size == 10
        assert out.anchor_ref  == frame.anchor_ref


# ── CapsFrame ────────────────────────────────────────────────────────────────

class TestCapsFrameCodec:
    def test_roundtrip(self, codec: NpsFrameCodec):
        frame = CapsFrame(
            anchor_ref="sha256:" + "e" * 64,
            count=2,
            data=({"id": 1}, {"id": 2}),
            next_cursor="cursor-abc",
            token_est=150,
            cached=True,
            tokenizer_used="cl100k_base",
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, CapsFrame)
        assert out.count          == 2
        assert out.next_cursor    == "cursor-abc"
        assert out.token_est      == 150
        assert out.cached         is True
        assert out.tokenizer_used == "cl100k_base"

    def test_minimal_caps(self, codec: NpsFrameCodec):
        frame = CapsFrame(anchor_ref="sha256:" + "f" * 64, count=0, data=())
        out   = codec.decode(codec.encode(frame))
        assert out.next_cursor is None
        assert out.token_est   is None


# ── ErrorFrame ────────────────────────────────────────────────────────────────

class TestErrorFrameCodec:
    def test_roundtrip(self, codec: NpsFrameCodec):
        frame = ErrorFrame(
            status="NPS-CLIENT-NOT-FOUND",
            error="NCP-ANCHOR-NOT-FOUND",
            message="anchor_ref references an unknown schema.",
            details={"anchor_ref": "sha256:unknown"},
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, ErrorFrame)
        assert out.status  == "NPS-CLIENT-NOT-FOUND"
        assert out.error   == "NCP-ANCHOR-NOT-FOUND"
        assert out.message == "anchor_ref references an unknown schema."

    def test_minimal_error(self, codec: NpsFrameCodec):
        frame = ErrorFrame(status="NPS-SERVER-INTERNAL", error="NCP-STREAM-TIMEOUT")
        out   = codec.decode(codec.encode(frame))
        assert out.message is None
        assert out.details is None


# ── NpsFrameCodec edge cases ─────────────────────────────────────────────────

class TestNpsFrameCodecEdgeCases:
    def test_peek_header(self, anchor: AnchorFrame, codec: NpsFrameCodec):
        wire   = codec.encode(anchor)
        header = NpsFrameCodec.peek_header(wire)
        assert header.frame_type == FrameType.ANCHOR

    def test_unsupported_tier_raises(self, codec: NpsFrameCodec, anchor: AnchorFrame):
        # Pass a raw int (not JSON=0 or MSGPACK=1) to trigger the unsupported-tier error
        with pytest.raises(NpsCodecError):
            codec._select_codec(0x02)  # type: ignore[arg-type]

    def test_unknown_frame_type_raises(self, codec: NpsFrameCodec):
        # Craft a wire message with an unregistered frame type (0x30 = ANNOUNCE, not in default registry)
        import struct
        payload = b'{"test": 1}'
        flags   = int(FrameFlags.TIER1_JSON | FrameFlags.FINAL)
        wire    = struct.pack(">BBH", 0x30, flags, len(payload)) + payload
        with pytest.raises(Exception):  # NpsFrameError or NpsCodecError
            codec.decode(wire)


# ── Codec error paths ─────────────────────────────────────────────────────────

class _BadSerialFrame:
    """Minimal NpsFrame-like object whose to_dict() returns non-serialisable data."""
    @property
    def frame_type(self) -> FrameType:
        return FrameType.ANCHOR

    def to_dict(self) -> dict:
        return {"bad": {1, 2, 3}}  # set is not JSON- or MsgPack-serialisable


class TestCodecErrorPaths:
    def test_tier1_json_encode_error(self, registry: FrameRegistry):
        j     = Tier1JsonCodec()
        frame = _BadSerialFrame()
        with pytest.raises(NpsCodecError, match="Tier-1 JSON encode failed"):
            j.encode(frame)  # type: ignore[arg-type]

    def test_tier1_json_decode_error(self, registry: FrameRegistry):
        j = Tier1JsonCodec()
        with pytest.raises(NpsCodecError, match="Tier-1 JSON decode failed"):
            j.decode(FrameType.ANCHOR, b"not valid json!!!", registry)

    def test_tier2_msgpack_encode_error(self, registry: FrameRegistry):
        m     = Tier2MsgPackCodec()
        frame = _BadSerialFrame()
        with pytest.raises(NpsCodecError, match="Tier-2 MsgPack encode failed"):
            m.encode(frame)  # type: ignore[arg-type]

    def test_tier2_msgpack_decode_error(self, registry: FrameRegistry):
        m = Tier2MsgPackCodec()
        with pytest.raises(NpsCodecError, match="Tier-2 MsgPack decode failed"):
            # \xc1 is a reserved / always-invalid MsgPack byte
            m.decode(FrameType.ANCHOR, b"\xc1\xff\xfe\x00invalid", registry)

    def test_payload_too_large_raises(self, registry: FrameRegistry, anchor: AnchorFrame):
        tiny_codec = NpsFrameCodec(registry, max_payload=10)
        with pytest.raises(NpsCodecError, match="exceeds max_frame_payload"):
            tiny_codec.encode(anchor)

    def test_dispatcher_reraises_nps_codec_error(self, registry: FrameRegistry):
        """NpsFrameCodec.encode must re-raise NpsCodecError from a tier codec (lines 155-156)."""
        codec = NpsFrameCodec(registry)
        frame = _BadSerialFrame()
        # override_tier=JSON forces Tier1JsonCodec.encode, which raises NpsCodecError
        with pytest.raises(NpsCodecError, match="Tier-1 JSON encode failed"):
            codec.encode(frame, override_tier=EncodingTier.JSON)  # type: ignore[arg-type]

    def test_extended_header_set_when_payload_exceeds_64k(self, registry: FrameRegistry):
        large_codec = NpsFrameCodec(registry, max_payload=200_000)
        # Build a CapsFrame whose JSON payload is > 64 KiB
        big_data = tuple({"id": i, "name": "x" * 200} for i in range(300))
        frame = CapsFrame(anchor_ref="sha256:" + "a" * 64, count=len(big_data), data=big_data)
        wire   = large_codec.encode(frame, override_tier=EncodingTier.JSON)
        assert len(wire) > 0xFFFF + 8  # header + payload exceeds 64 KiB
        header = NpsFrameCodec.peek_header(wire)
        assert header.is_extended  # EXT flag must be set
