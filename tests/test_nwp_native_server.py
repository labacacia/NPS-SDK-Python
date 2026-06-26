# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import CapsFrame, ErrorFrame
from nps_sdk.nwp import ActionFrame, NwpNativeNodeServer, QueryFrame


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

    out = await server.dispatch_wire(codec.encode(QueryFrame(anchor_ref="sha256:a"), override_tier=EncodingTier.MSGPACK))
    frame = codec.decode(out)

    assert isinstance(frame, CapsFrame)
    assert frame.count == 1
    assert frame.data[0]["id"] == 42


@pytest.mark.asyncio
async def test_serve_once_reads_and_writes_one_frame() -> None:
    codec = NpsFrameCodec(FrameRegistry.create_full())
    server = NwpNativeNodeServer(action_handler=lambda frame: {"action": frame.action_id}, codec=codec)
    reader = asyncio.StreamReader()
    writer = FakeWriter()

    reader.feed_data(codec.encode(ActionFrame("ping"), override_tier=EncodingTier.MSGPACK))
    reader.feed_eof()

    assert await server.serve_once(reader, writer)
    frame = codec.decode(bytes(writer.body))

    assert isinstance(frame, CapsFrame)
    assert frame.data[0]["action"] == "ping"


def test_action_frame_accepts_legacy_action_key() -> None:
    frame = ActionFrame.from_dict({"action": "ping"})

    assert frame.action_id == "ping"


@pytest.mark.asyncio
async def test_unsupported_frame_returns_error_frame() -> None:
    server = NwpNativeNodeServer()
    frame = await server.dispatch(ErrorFrame("NPS-TEST", "TEST"))

    assert isinstance(frame, ErrorFrame)
    assert frame.error == "NWP-NATIVE-FRAME-UNSUPPORTED"
