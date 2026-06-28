# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Native-mode NWP node serving helpers over an established NCP byte stream."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from nps_sdk.core.codec import NpsFrame, NpsFrameCodec
from nps_sdk.core.frames import DEFAULT_HEADER_SIZE, EXTENDED_HEADER_SIZE, EncodingTier, FrameFlags, FrameHeader, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import CapsFrame, ErrorFrame
from nps_sdk.nwp.frames import ActionFrame, QueryFrame
from nps_sdk.nwp.memory_node_server import IMemoryNodeProvider, MemoryNodeOptions, MemoryNodeQueryResult


NativeQueryHandler = Callable[[QueryFrame], CapsFrame | MemoryNodeQueryResult | Sequence[Any] | Awaitable[CapsFrame | MemoryNodeQueryResult | Sequence[Any]]]
NativeActionHandler = Callable[[ActionFrame], NpsFrame | Any | Awaitable[NpsFrame | Any]]


class NwpNativeNodeServer:
    """
    Transport-agnostic native NWP serving loop.

    The stream is expected to be past NCP preamble/Hello negotiation. TLS,
    authentication, and session establishment stay owned by the caller or a
    future NCP session abstraction; this class only reads NPS frames, dispatches
    NWP QueryFrame/ActionFrame, and writes response frames.
    """

    def __init__(
        self,
        *,
        codec: NpsFrameCodec | None = None,
        registry: FrameRegistry | None = None,
        tier: EncodingTier = EncodingTier.MSGPACK,
        enabled_encodings: Sequence[str] | None = None,
        anchor_ref: str = "native:nwp",
        query_handler: NativeQueryHandler | None = None,
        action_handler: NativeActionHandler | None = None,
        memory_provider: IMemoryNodeProvider | None = None,
        memory_options: MemoryNodeOptions | None = None,
    ) -> None:
        self._registry = registry or FrameRegistry.create_full()
        self._codec = codec or NpsFrameCodec(self._registry)
        self._tier = tier
        self._enabled_encodings = tuple(enabled_encodings or (_encoding_token(tier),))
        self._anchor_ref = anchor_ref
        self._query_handler = query_handler
        self._action_handler = action_handler
        self._memory_provider = memory_provider
        self._memory_options = memory_options

    async def dispatch(self, frame: NpsFrame) -> NpsFrame:
        """Dispatch one decoded frame and return a response frame."""
        try:
            if isinstance(frame, QueryFrame):
                return await self._dispatch_query(frame)
            if isinstance(frame, ActionFrame):
                return await self._dispatch_action(frame)
            return ErrorFrame(
                status="NPS-CLIENT-BAD-FRAME",
                error="NWP-NATIVE-FRAME-UNSUPPORTED",
                message=f"Native NWP server does not handle frame type {frame.frame_type!r}.",
            )
        except Exception as exc:
            return ErrorFrame(
                status="NPS-SERVER-INTERNAL",
                error="NWP-NATIVE-DISPATCH-FAILED",
                message=str(exc),
            )

    async def dispatch_wire(self, wire: bytes) -> bytes:
        """Decode one wire frame, dispatch it, and return one encoded response frame."""
        policy_error = _policy_error(self._codec.peek_header(wire), self._tier, self._enabled_encodings)
        response = policy_error or await self.dispatch(self._codec.decode(wire))
        return self._codec.encode(response, override_tier=self._tier)

    async def serve_once(self, reader: Any, writer: Any) -> bool:
        """
        Read one frame from ``reader`` and write one response to ``writer``.

        Returns ``False`` on clean EOF before a new frame starts.
        """
        wire = await _read_wire_frame(reader)
        if wire is None:
            return False
        writer.write(await self.dispatch_wire(wire))
        drain = getattr(writer, "drain", None)
        if drain is not None:
            await drain()
        return True

    async def serve(self, reader: Any, writer: Any) -> None:
        """Serve frames until EOF."""
        while await self.serve_once(reader, writer):
            pass

    async def _dispatch_query(self, frame: QueryFrame) -> CapsFrame:
        if self._query_handler is not None:
            return _coerce_query_result(await _maybe_await(self._query_handler(frame)), self._anchor_ref)
        if self._memory_provider is not None and self._memory_options is not None:
            result = await self._memory_provider.query(frame, self._memory_options)
            return _coerce_query_result(result, self._anchor_ref)
        raise RuntimeError("No native NWP query handler or memory provider configured.")

    async def _dispatch_action(self, frame: ActionFrame) -> NpsFrame:
        if self._action_handler is None:
            raise RuntimeError("No native NWP action handler configured.")
        result = await _maybe_await(self._action_handler(frame))
        if isinstance(result, NpsFrame):
            return result
        if result is None:
            return CapsFrame(anchor_ref=self._anchor_ref, count=0, data=())
        return CapsFrame(anchor_ref=self._anchor_ref, count=1, data=(result,))


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_query_result(result: CapsFrame | MemoryNodeQueryResult | Sequence[Any], anchor_ref: str) -> CapsFrame:
    if isinstance(result, CapsFrame):
        return result
    if isinstance(result, MemoryNodeQueryResult):
        return CapsFrame(
            anchor_ref=anchor_ref,
            count=len(result.rows),
            data=tuple(result.rows),
            next_cursor=result.next_cursor,
            token_est=_estimate_tokens(result.rows),
            tokenizer_used="native-estimate",
        )
    return CapsFrame(
        anchor_ref=anchor_ref,
        count=len(result),
        data=tuple(result),
        token_est=_estimate_tokens(result),
        tokenizer_used="native-estimate",
    )


def _estimate_tokens(rows: Sequence[Any]) -> int:
    import json

    return max(1, len(json.dumps(list(rows), separators=(",", ":"))) // 4)


def _policy_error(header: FrameHeader, default_tier: EncodingTier, enabled_encodings: Sequence[str]) -> ErrorFrame | None:
    if header.encoding_tier == default_tier:
        return None
    if (
        header.encoding_tier == EncodingTier.BINARY_VECTOR
        and "binary_vector.v1" in enabled_encodings
        and header.frame_type == FrameType.QUERY
    ):
        return None
    return ErrorFrame(
        status="NPS-SERVER-ENCODING-UNSUPPORTED",
        error="NCP-ENCODING-UNSUPPORTED",
        message=(
            f"Frame type 0x{int(header.frame_type):02X} used {_encoding_token(header.encoding_tier)}, "
            f"but the negotiated policy allows {', '.join(enabled_encodings)}."
        ),
    )


def _encoding_token(tier: EncodingTier) -> str:
    if tier == EncodingTier.JSON:
        return "json"
    if tier == EncodingTier.MSGPACK:
        return "msgpack"
    if tier == EncodingTier.BINARY_VECTOR:
        return "binary_vector.v1"
    return f"unknown:{int(tier)}"


async def _read_wire_frame(reader: Any) -> bytes | None:
    try:
        head = await reader.readexactly(DEFAULT_HEADER_SIZE)
    except EOFError:
        return None
    except Exception as exc:
        if exc.__class__.__name__ == "IncompleteReadError" and not getattr(exc, "partial", b""):
            return None
        raise

    raw_header = head
    if head[1] & int(FrameFlags.EXT):
        raw_header += await reader.readexactly(EXTENDED_HEADER_SIZE - DEFAULT_HEADER_SIZE)
    header = FrameHeader.parse(raw_header)
    payload = await reader.readexactly(header.payload_length)
    return raw_header + payload
