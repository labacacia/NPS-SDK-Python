# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NCP native-mode TCP server. Listens on a configured endpoint, validates the
connection preamble, reads the client's HelloFrame, and yields an
:class:`NcpServerConnection` for the application to accept or reject (NPS-1 §4.6).

Mirrors the .NET ``NcpServer`` / ``NcpServerConnection`` pair, adapted to
asyncio streams.
"""

from __future__ import annotations

import asyncio
import dataclasses

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.exceptions import (
    NpsEncodingUnsupportedError,
    NpsFrameError,
)
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp import preamble
from nps_sdk.ncp.encoding_policy import NcpEncodingPolicy
from nps_sdk.ncp.frames import ErrorFrame, HelloFrame, NcpHandshakeCapsFrame
from nps_sdk.ncp.server_options import NcpServerOptions
from nps_sdk.ncp.session import NcpSession, read_frame_header


def _hello_registry() -> FrameRegistry:
    return FrameRegistry({FrameType.HELLO: HelloFrame})


class NcpServerConnection:
    """
    Server-side representation of an inbound NCP connection that has passed the
    preamble check and sent its :class:`HelloFrame`. Call :meth:`accept` to
    complete the handshake, or :meth:`reject` to send an error and close.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        codec: NpsFrameCodec,
        client_hello: HelloFrame,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._codec = codec
        self.client_hello = client_hello

    async def accept(self, server_caps: NcpHandshakeCapsFrame) -> NcpSession:
        """
        Send *server_caps* to the client and return a live :class:`NcpSession`.
        The encoding policy is negotiated from the client's supported encodings.
        """
        policy = self._negotiate_encoding_policy(self.client_hello)
        caps = dataclasses.replace(
            server_caps,
            negotiated_encoding=NcpEncodingPolicy.encoding_token(policy.default_tier),
            enabled_encodings=policy.enabled_encodings,
        )
        wire = self._codec.encode(caps, override_tier=policy.default_tier)
        self._writer.write(wire)
        await self._writer.drain()
        return NcpSession(self._reader, self._writer, self._codec, caps, policy)

    async def reject(self, error: ErrorFrame) -> None:
        """Send an :class:`ErrorFrame` to reject the client and close the connection."""
        try:
            wire = self._codec.encode(error, override_tier=EncodingTier.JSON)
            self._writer.write(wire)
            await self._writer.drain()
        finally:
            await self.close()

    async def close(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    @staticmethod
    def _negotiate_encoding_policy(hello: HelloFrame) -> NcpEncodingPolicy:
        binary_vector_enabled = "binary_vector.v1" in hello.supported_encodings
        for enc in hello.supported_encodings:
            if enc == "msgpack":
                return NcpEncodingPolicy(EncodingTier.MSGPACK, binary_vector_enabled)
            if enc == "json":
                return NcpEncodingPolicy(EncodingTier.JSON, binary_vector_enabled)
        raise NpsEncodingUnsupportedError(
            "Client did not offer a supported stable default encoding "
            "(expected msgpack or json)."
        )


class NcpServer:
    """NCP native-mode TCP server over asyncio."""

    def __init__(
        self,
        host: str,
        port: int,
        codec: NpsFrameCodec,
        options: NcpServerOptions | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._codec = codec
        self._options = options or NcpServerOptions()
        self._server: asyncio.AbstractServer | None = None
        self._pending: asyncio.Queue[NcpServerConnection] = asyncio.Queue()

    @property
    def port(self) -> int:
        """The bound port (useful when constructed with port 0)."""
        if self._server is None:
            return self._port
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        """Start listening so :meth:`accept_connection` can be called."""
        self._server = await asyncio.start_server(
            self._on_client, self._host, self._port
        )

    async def stop(self) -> None:
        """Stop the listener and release the port binding."""
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except (ConnectionError, OSError):
                pass
            self._server = None

    async def accept_connection(self) -> NcpServerConnection:
        """
        Return the next inbound connection that has passed the preamble check and
        sent its HelloFrame, ready to be accepted or rejected.
        """
        return await self._pending.get()

    async def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            reader, writer = await self._authenticate(reader, writer)
            timeout = self._options.handshake_read_timeout
            conn = await asyncio.wait_for(
                self._handshake(reader, writer),
                timeout=timeout if timeout and timeout > 0 else None,
            )
            await self._pending.put(conn)
        except Exception:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> NcpServerConnection:
        # 1 — read & validate preamble
        preamble_buf = await reader.readexactly(preamble.LENGTH)
        preamble.validate(preamble_buf)  # raises NcpPreambleInvalidError on mismatch

        # 2 — read frame header
        header, raw = await read_frame_header(reader)
        if header.frame_type != FrameType.HELLO:
            raise NpsFrameError(
                f"Expected HelloFrame (0x{int(FrameType.HELLO):02X}) as first "
                f"frame after preamble, got 0x{int(header.frame_type):02X}."
            )
        if header.payload_length > self._options.max_hello_payload:
            raise NpsFrameError(
                f"HelloFrame payload length {header.payload_length} exceeds "
                f"configured maximum {self._options.max_hello_payload} bytes."
            )

        # 3 — read payload and deserialise HelloFrame
        payload = await reader.readexactly(header.payload_length)
        hello_codec = NpsFrameCodec(_hello_registry())
        hello = hello_codec.decode(raw + payload)
        assert isinstance(hello, HelloFrame)
        return NcpServerConnection(reader, writer, self._codec, hello)

    async def _authenticate(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._options.authenticate is None:
            if self._options.require_authenticated_stream:
                raise NpsFrameError(
                    "NcpServerOptions.require_authenticated_stream is true, but no "
                    "authenticate hook is configured."
                )
            return reader, writer
        new_reader, new_writer = await self._options.authenticate(reader, writer)
        if new_reader is None or new_writer is None:
            raise NpsFrameError("NCP stream authentication hook returned null.")
        if self._options.require_authenticated_stream and new_writer is writer:
            raise NpsFrameError(
                "NCP stream authentication hook returned the original stream while "
                "require_authenticated_stream is true."
            )
        return new_reader, new_writer

    async def __aenter__(self) -> "NcpServer":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()
