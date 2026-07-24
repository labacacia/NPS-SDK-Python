# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
A live NCP native-mode session established after a successful handshake.

Wraps the underlying asyncio TCP streams and exposes the negotiated parameters.
Frames are sent/received using the negotiated :class:`NcpEncodingPolicy`.
Mirrors the .NET ``NcpSession``.
"""

from __future__ import annotations

import asyncio

from nps_sdk.core.codec import NpsFrame, NpsFrameCodec
from nps_sdk.core.frames import EncodingTier, FrameFlags, FrameHeader
from nps_sdk.ncp.encoding_policy import NcpEncodingPolicy
from nps_sdk.ncp.frames import NcpHandshakeCapsFrame


async def read_frame_header(reader: asyncio.StreamReader) -> tuple[FrameHeader, bytes]:
    """
    Read a frame header from *reader*, peeking the EXT flag to decide whether to
    read 4 or 8 bytes total. Mirrors ``NcpNativeClient.ReadFrameHeaderAsync``.
    """
    from nps_sdk.core.frames import DEFAULT_HEADER_SIZE, EXTENDED_HEADER_SIZE

    peek = await reader.readexactly(2)
    ext = bool(FrameFlags(peek[1]) & FrameFlags.EXT)
    remaining = (EXTENDED_HEADER_SIZE if ext else DEFAULT_HEADER_SIZE) - 2
    rest = await reader.readexactly(remaining)
    raw = peek + rest
    return FrameHeader.parse(raw), raw


class NcpSession:
    """A live NCP native-mode session over an asyncio TCP connection."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        codec: NpsFrameCodec,
        server_caps: NcpHandshakeCapsFrame,
        encoding_policy: NcpEncodingPolicy,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._codec = codec
        self._server_caps = server_caps
        self._encoding_policy = encoding_policy
        self._closed = False

    # ── Negotiated parameters ──────────────────────────────────────────────────

    @property
    def server_caps(self) -> NcpHandshakeCapsFrame:
        """Capabilities the peer advertised during the handshake."""
        return self._server_caps

    @property
    def encoding_policy(self) -> NcpEncodingPolicy:
        """Encoding policy negotiated during the handshake."""
        return self._encoding_policy

    @property
    def negotiated_tier(self) -> EncodingTier:
        """Stable default encoding tier negotiated during the handshake."""
        return self._encoding_policy.default_tier

    @property
    def is_connected(self) -> bool:
        """``True`` while the underlying connection is still open."""
        return not self._closed and not self._writer.is_closing()

    # ── Frame exchange ─────────────────────────────────────────────────────────

    async def send_frame(
        self,
        frame: NpsFrame,
        *,
        tier: EncodingTier | None = None,
    ) -> None:
        """
        Encode and send *frame* over the session.

        The encoding tier defaults to the negotiated stable tier; an explicit
        *tier* is validated against the negotiated policy before use.
        """
        chosen = tier if tier is not None else self._encoding_policy.default_tier
        header = FrameHeader(frame.frame_type, FrameFlags(int(chosen)), 0)
        self._encoding_policy.ensure_allows(header)
        wire = self._codec.encode(frame, override_tier=chosen)
        self._writer.write(wire)
        await self._writer.drain()

    async def receive_frame(self) -> NpsFrame:
        """Read the next frame from the session, honouring the negotiated policy."""
        header, raw = await read_frame_header(self._reader)
        payload = await self._reader.readexactly(header.payload_length)
        self._encoding_policy.ensure_allows(header)
        return self._codec.decode(raw + payload)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying connection. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def __aenter__(self) -> "NcpSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
