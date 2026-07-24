# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
End-to-end NCP native-mode transport tests: the REAL NcpServer driven by the
REAL NcpNativeClient over a loopback TCP socket (asyncio).
"""

import asyncio

import pytest

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.client import NcpHandshakeError, NcpNativeClient
from nps_sdk.ncp.frames import (
    ErrorFrame,
    HelloFrame,
    NcpHandshakeCapsFrame,
    StreamFrame,
)
from nps_sdk.ncp.server import NcpServer
from nps_sdk.ncp.session import read_frame_header


def _codec(max_payload: int = 0xFFFF) -> NpsFrameCodec:
    return NpsFrameCodec(FrameRegistry.create_full(), max_payload=max_payload)


def _hello(encodings=("msgpack", "json")) -> HelloFrame:
    return HelloFrame(
        nps_version="0.4",
        supported_encodings=tuple(encodings),
        supported_protocols=("ncp", "nwp"),
        agent_id="urn:nps:agent:example.com:agent01",
    )


def _server_caps() -> NcpHandshakeCapsFrame:
    return NcpHandshakeCapsFrame(
        node_id="urn:nps:agent:example.com:node01",
        caps=("ncp", "nwp", "nip"),
    )


async def _start_server(options=None) -> NcpServer:
    server = NcpServer("127.0.0.1", 0, _codec(), options)
    await server.start()
    return server


async def test_handshake_happy_path_msgpack() -> None:
    server = await _start_server()
    try:
        async def serve():
            conn = await server.accept_connection()
            assert conn.client_hello.agent_id == "urn:nps:agent:example.com:agent01"
            return await conn.accept(_server_caps())

        server_task = asyncio.create_task(serve())

        client = NcpNativeClient(_codec())
        session = await client.connect("127.0.0.1", server.port, _hello())

        server_session = await server_task
        # msgpack is highest priority stable default
        assert session.negotiated_tier == EncodingTier.MSGPACK
        assert session.server_caps.node_id == "urn:nps:agent:example.com:node01"
        assert session.server_caps.negotiated_encoding == "msgpack"
        assert session.is_connected

        await session.close()
        await server_session.close()
    finally:
        await server.stop()


async def test_handshake_negotiates_json_when_only_json_offered() -> None:
    server = await _start_server()
    try:
        async def serve():
            conn = await server.accept_connection()
            return await conn.accept(_server_caps())

        server_task = asyncio.create_task(serve())
        client = NcpNativeClient(_codec())
        session = await client.connect(
            "127.0.0.1", server.port, _hello(encodings=("json",))
        )
        server_session = await server_task
        assert session.negotiated_tier == EncodingTier.JSON
        assert session.server_caps.negotiated_encoding == "json"
        await session.close()
        await server_session.close()
    finally:
        await server.stop()


async def test_binary_vector_extension_enabled_in_policy() -> None:
    server = await _start_server()
    try:
        async def serve():
            conn = await server.accept_connection()
            return await conn.accept(_server_caps())

        server_task = asyncio.create_task(serve())
        client = NcpNativeClient(_codec())
        session = await client.connect(
            "127.0.0.1",
            server.port,
            _hello(encodings=("msgpack", "json", "binary_vector.v1")),
        )
        server_session = await server_task
        assert session.encoding_policy.binary_vector_enabled
        assert "binary_vector.v1" in session.server_caps.enabled_encodings
        await session.close()
        await server_session.close()
    finally:
        await server.stop()


async def test_server_rejection_raises_handshake_error() -> None:
    server = await _start_server()
    try:
        async def serve():
            conn = await server.accept_connection()
            await conn.reject(
                ErrorFrame(
                    status="NPS-PROTO-VERSION-INCOMPATIBLE",
                    error="NCP-VERSION-INCOMPATIBLE",
                    message="server too old",
                )
            )

        server_task = asyncio.create_task(serve())
        client = NcpNativeClient(_codec())
        with pytest.raises(NcpHandshakeError) as excinfo:
            await client.connect("127.0.0.1", server.port, _hello())
        assert excinfo.value.error == "NCP-VERSION-INCOMPATIBLE"
        assert excinfo.value.message == "server too old"
        await server_task
    finally:
        await server.stop()


async def test_frame_exchange_over_live_session() -> None:
    server = await _start_server()
    try:
        async def serve():
            conn = await server.accept_connection()
            session = await conn.accept(_server_caps())
            frame = await session.receive_frame()
            assert isinstance(frame, StreamFrame)
            # echo back
            await session.send_frame(frame)
            return session

        server_task = asyncio.create_task(serve())
        client = NcpNativeClient(_codec())
        session = await client.connect("127.0.0.1", server.port, _hello())

        outbound = StreamFrame(
            stream_id="s-1", seq=0, is_last=True, data=({"row": 1},)
        )
        await session.send_frame(outbound)
        echoed = await session.receive_frame()
        assert isinstance(echoed, StreamFrame)
        assert echoed.stream_id == "s-1"
        assert echoed.data == ({"row": 1},)

        server_session = await server_task
        await session.close()
        await server_session.close()
    finally:
        await server.stop()


async def test_ext_header_round_trip_over_session() -> None:
    """A payload > 64 KiB forces the EXT 8-byte header; it must round-trip."""
    # A codec whose max_payload exceeds 64 KiB so EXT frames can be produced.
    big_codec = _codec(max_payload=1 << 20)
    server = NcpServer("127.0.0.1", 0, big_codec)
    await server.start()
    try:
        async def serve():
            conn = await server.accept_connection()
            session = await conn.accept(_server_caps())
            frame = await session.receive_frame()
            await session.send_frame(frame)
            return session

        server_task = asyncio.create_task(serve())
        client = NcpNativeClient(big_codec)
        session = await client.connect("127.0.0.1", server.port, _hello())

        big_rows = tuple({"i": i, "pad": "x" * 64} for i in range(2000))
        outbound = StreamFrame(stream_id="big", seq=0, is_last=True, data=big_rows)
        await session.send_frame(outbound)
        echoed = await session.receive_frame()
        assert isinstance(echoed, StreamFrame)
        assert len(echoed.data) == 2000

        server_session = await server_task
        await session.close()
        await server_session.close()
    finally:
        await server.stop()


async def test_ext_header_read_helper_round_trip() -> None:
    """read_frame_header must handle both default (4B) and extended (8B) headers."""
    from nps_sdk.core.frames import FrameFlags, FrameHeader

    async def echo_headers(reader, writer):
        default = FrameHeader(FrameType.CAPS, FrameFlags.TIER1_JSON, 3)
        writer.write(default.to_bytes() + b"abc")
        extended = FrameHeader(FrameType.CAPS, FrameFlags.EXT, 3)
        writer.write(extended.to_bytes() + b"xyz")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(echo_headers, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        h1, raw1 = await read_frame_header(reader)
        assert not h1.is_extended and len(raw1) == 4
        assert await reader.readexactly(h1.payload_length) == b"abc"
        h2, raw2 = await read_frame_header(reader)
        assert h2.is_extended and len(raw2) == 8
        assert await reader.readexactly(h2.payload_length) == b"xyz"
        writer.close()


async def test_preamble_rejection_drops_connection() -> None:
    """A client that sends a bad preamble never becomes an accepted connection."""
    server = await _start_server()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(b"GARBAGE!")  # 8 bytes, not the preamble
        await writer.drain()
        # server drops the connection; a subsequent read yields EOF
        data = await reader.read()
        assert data == b""
        writer.close()
    finally:
        await server.stop()


async def test_unexpected_first_frame_is_not_accepted() -> None:
    """Valid preamble but a non-Hello first frame must not be accepted."""
    server = await _start_server()
    try:
        from nps_sdk.ncp import preamble

        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        await preamble.write_async(writer)
        # send an ErrorFrame instead of a HelloFrame
        wire = _codec().encode(
            ErrorFrame(status="X", error="Y"), override_tier=EncodingTier.JSON
        )
        writer.write(wire)
        await writer.drain()
        data = await reader.read()
        assert data == b""
        writer.close()
    finally:
        await server.stop()
