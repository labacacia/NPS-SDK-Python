# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import CapsFrame, ErrorFrame
from nps_sdk.nwp import ActionFrame, NwpNativeNodeServer, QueryFrame, VectorSearchOptions


class FakeWriter:
    def __init__(self) -> None:
        self.body = bytearray()

    def write(self, chunk: bytes) -> None:
        self.body.extend(chunk)

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_dispatch_wire_returns_caps_frame_for_query() -> None:
    codec = NpsFrameCodec(FrameRegistry.create_full())
    server = NwpNativeNodeServer(query_handler=lambda _: [{"id": 42}], codec=codec)

    out = await server.dispatch_wire(codec.encode(QueryFrame(anchor_ref="sha256:a", request_id="req-query-1"), override_tier=EncodingTier.MSGPACK))
    frame = codec.decode(out)

    assert isinstance(frame, CapsFrame)
    assert frame.count == 1
    assert frame.data[0]["id"] == 42
    assert frame.request_id == "req-query-1"


@pytest.mark.asyncio
async def test_serve_once_reads_and_writes_one_frame() -> None:
    codec = NpsFrameCodec(FrameRegistry.create_full())
    server = NwpNativeNodeServer(action_handler=lambda frame: {"action": frame.action_id}, codec=codec)
    reader = asyncio.StreamReader()
    writer = FakeWriter()

    reader.feed_data(codec.encode(ActionFrame("ping", request_id="req-action-1"), override_tier=EncodingTier.MSGPACK))
    reader.feed_eof()

    assert await server.serve_once(reader, writer)
    frame = codec.decode(bytes(writer.body))

    assert isinstance(frame, CapsFrame)
    assert frame.data[0]["action"] == "ping"
    assert frame.request_id == "req-action-1"


@pytest.mark.asyncio
async def test_dispatch_wire_rejects_unnegotiated_binary_vector() -> None:
    codec = NpsFrameCodec(FrameRegistry.create_full())
    server = NwpNativeNodeServer(query_handler=lambda _: [{"id": 42}], codec=codec)
    request = codec.encode(_vector_query(), override_tier=EncodingTier.BINARY_VECTOR)

    frame = codec.decode(await server.dispatch_wire(request))

    assert isinstance(frame, ErrorFrame)
    assert frame.status == "NPS-SERVER-ENCODING-UNSUPPORTED"
    assert frame.error == "NCP-ENCODING-UNSUPPORTED"


@pytest.mark.asyncio
async def test_dispatch_wire_allows_negotiated_binary_vector_query() -> None:
    codec = NpsFrameCodec(FrameRegistry.create_full())
    server = NwpNativeNodeServer(
        query_handler=lambda _: [{"id": 42}],
        codec=codec,
        enabled_encodings=("msgpack", "binary_vector.v1"),
    )
    request = codec.encode(_vector_query(), override_tier=EncodingTier.BINARY_VECTOR)

    frame = codec.decode(await server.dispatch_wire(request))

    assert isinstance(frame, CapsFrame)
    assert frame.count == 1


def test_action_frame_accepts_legacy_action_key() -> None:
    frame = ActionFrame.from_dict({"action": "ping"})

    assert frame.action_id == "ping"


@pytest.mark.asyncio
async def test_unsupported_frame_returns_error_frame() -> None:
    server = NwpNativeNodeServer()
    frame = await server.dispatch(ErrorFrame("NPS-TEST", "TEST"))

    assert isinstance(frame, ErrorFrame)
    assert frame.error == "NWP-NATIVE-FRAME-UNSUPPORTED"


def _vector_query() -> QueryFrame:
    return QueryFrame(
        vector_search=VectorSearchOptions(
            field="embedding",
            vector=(0.25, -1.5, 3.0),
            top_k=1,
        ),
    )
