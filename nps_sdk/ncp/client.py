# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NCP native-mode TCP client.

Performs the 3-step handshake (preamble → HelloFrame → NcpHandshakeCapsFrame)
per NPS-1 §4.6 and returns a live :class:`NcpSession`. Mirrors the .NET
``NcpNativeClient``.
"""

from __future__ import annotations

import asyncio

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.exceptions import NpsCodecError
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp import preamble
from nps_sdk.ncp.encoding_policy import NcpEncodingPolicy
from nps_sdk.ncp.frames import ErrorFrame, HelloFrame, NcpHandshakeCapsFrame
from nps_sdk.ncp.session import NcpSession, read_frame_header


class NcpHandshakeError(Exception):
    """
    Raised when the server rejects the native-mode handshake or sends an
    unexpected frame. Mirrors the .NET ``NcpHandshakeException``.
    """

    def __init__(self, error: str, message: str | None = None) -> None:
        super().__init__(message or error)
        self.error = error
        self.message = message


def _handshake_registry() -> FrameRegistry:
    """Registry that resolves handshake response frame types (Caps → handshake caps)."""
    return FrameRegistry(
        {
            FrameType.CAPS: NcpHandshakeCapsFrame,
            FrameType.ERROR: ErrorFrame,
        }
    )


class NcpNativeClient:
    """NCP native-mode TCP client performing the handshake over asyncio streams."""

    def __init__(self, codec: NpsFrameCodec) -> None:
        self._codec = codec
        # Dedicated codec that decodes the Caps response as a handshake caps frame.
        self._handshake_codec = NpsFrameCodec(_handshake_registry())

    async def connect(
        self,
        host: str,
        port: int,
        hello: HelloFrame,
    ) -> NcpSession:
        """
        Open a TCP connection to *host*:*port*, perform the NCP native-mode
        handshake, and return a live session.

        :raises NcpHandshakeError: server rejected the handshake or sent an
            unexpected frame.
        """
        reader, writer = await asyncio.open_connection(host, port)
        try:
            # 1 — preamble (Tier-1 JSON encoding not yet negotiated)
            await preamble.write_async(writer)

            # 2 — HelloFrame (always JSON per spec — encoding not yet agreed)
            hello_wire = self._codec.encode(hello, override_tier=EncodingTier.JSON)
            writer.write(hello_wire)
            await writer.drain()

            # 3 — read server response header (handles EXT flag)
            header, raw = await read_frame_header(reader)

            # 4 — read payload
            payload = await reader.readexactly(header.payload_length)
            wire = raw + payload

            # 5 — ErrorFrame → raise
            if header.frame_type == FrameType.ERROR:
                err = self._handshake_codec.decode(wire)
                assert isinstance(err, ErrorFrame)
                raise NcpHandshakeError(err.error, err.message)

            if header.frame_type != FrameType.CAPS:
                raise NcpHandshakeError(
                    "NCP-HANDSHAKE-UNEXPECTED-FRAME",
                    f"Expected CapsFrame (0x{int(FrameType.CAPS):02X}), "
                    f"got 0x{int(header.frame_type):02X}.",
                )

            # 6 — decode NcpHandshakeCapsFrame using the tier the server signalled
            # as the stable default in the response header flags.
            negotiated_tier = header.encoding_tier
            caps = self._handshake_codec.decode(wire)
            assert isinstance(caps, NcpHandshakeCapsFrame)
            policy = NcpEncodingPolicy.from_enabled_encodings(
                negotiated_tier, caps.enabled_encodings
            )
            return NcpSession(reader, writer, self._codec, caps, policy)
        except NcpHandshakeError:
            writer.close()
            raise
        except (NpsCodecError, Exception):
            writer.close()
            raise
