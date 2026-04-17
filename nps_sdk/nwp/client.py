# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NwpClient — async HTTP-mode client for NPS Neural Web Protocol nodes.

Communicates with a Memory Node or Action Node via HTTP Overlay mode (NPS-2 §3).
Wire format is NPS frames sent as application/x-nps-frame request/response bodies.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import AnchorFrame, CapsFrame, StreamFrame
from nps_sdk.nwp.frames import ActionFrame, AsyncActionResponse, QueryFrame


_CONTENT_TYPE = "application/x-nps-frame"
_ACCEPT       = "application/x-nps-frame"


class NwpClient:
    """
    Async client for NWP HTTP-mode nodes (NPS-2).

    Usage::

        async with NwpClient("https://node.example.com") as client:
            caps = await client.query(
                QueryFrame(anchor_ref="sha256:...", limit=50)
            )

    The client encodes outgoing frames as Tier-2 MsgPack by default.
    It decodes responses using the full NCP + NWP frame registry.
    """

    def __init__(
        self,
        base_url: str,
        *,
        default_tier: EncodingTier = EncodingTier.MSGPACK,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
        registry: FrameRegistry | None = None,
    ) -> None:
        self._base_url    = base_url.rstrip("/")
        self._tier        = default_tier
        self._registry    = registry or FrameRegistry.create_full()
        self._codec       = NpsFrameCodec(self._registry)
        self._timeout     = timeout
        self._owns_client = http_client is None
        self._http        = http_client or httpx.AsyncClient(timeout=timeout)

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "NwpClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client:
            await self._http.aclose()

    # ── Public API ────────────────────────────────────────────────────────────

    async def send_anchor(self, frame: AnchorFrame) -> None:
        """
        Send an AnchorFrame to the node's /anchor endpoint.
        Nodes store the schema and reference it via anchor_id in subsequent requests.
        """
        wire = self._codec.encode(frame, override_tier=self._tier)
        response = await self._http.post(
            f"{self._base_url}/anchor",
            content=wire,
            headers={"Content-Type": _CONTENT_TYPE, "Accept": _ACCEPT},
        )
        response.raise_for_status()

    async def query(self, frame: QueryFrame) -> CapsFrame:
        """
        Send a QueryFrame to the node's /query endpoint and return the CapsFrame response.

        Raises:
            httpx.HTTPStatusError: on HTTP 4xx / 5xx.
            NpsCodecError: if the response cannot be decoded.
        """
        wire     = self._codec.encode(frame, override_tier=self._tier)
        response = await self._http.post(
            f"{self._base_url}/query",
            content=wire,
            headers={"Content-Type": _CONTENT_TYPE, "Accept": _ACCEPT},
        )
        response.raise_for_status()

        result = self._codec.decode(response.content)
        if not isinstance(result, CapsFrame):
            raise TypeError(
                f"Expected CapsFrame response from /query, got {type(result).__name__}."
            )
        return result

    async def stream(self, frame: QueryFrame) -> AsyncIterator[StreamFrame]:
        """
        Send a QueryFrame to the node's /stream endpoint and yield StreamFrame chunks
        as they arrive (newline-delimited NPS frames, NPS-2 §4.2).
        """
        wire = self._codec.encode(frame, override_tier=self._tier)
        async with self._http.stream(
            "POST",
            f"{self._base_url}/stream",
            content=wire,
            headers={"Content-Type": _CONTENT_TYPE, "Accept": _ACCEPT},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if not chunk:  # pragma: no cover — keep-alive empty chunks
                    continue
                result = self._codec.decode(chunk)
                if not isinstance(result, StreamFrame):
                    raise TypeError(
                        f"Expected StreamFrame chunk from /stream, got {type(result).__name__}."
                    )
                yield result
                if result.is_last:  # pragma: no cover — final-frame break
                    break

    async def invoke(self, frame: ActionFrame) -> Any:
        """
        Send an ActionFrame to the node's /invoke endpoint.

        Returns:
            For synchronous actions: the raw JSON-decoded response body.
            For async actions (frame.async_ is True): an AsyncActionResponse.
        """
        wire     = self._codec.encode(frame, override_tier=self._tier)
        response = await self._http.post(
            f"{self._base_url}/invoke",
            content=wire,
            headers={"Content-Type": _CONTENT_TYPE, "Accept": _ACCEPT},
        )
        response.raise_for_status()

        if frame.async_:
            import json
            return AsyncActionResponse.from_dict(json.loads(response.content))

        # Synchronous actions return the result as a raw NPS frame or plain JSON
        content_type = response.headers.get("content-type", "")
        if _ACCEPT in content_type:
            return self._codec.decode(response.content)
        import json
        return json.loads(response.content)

    async def close(self) -> None:
        """Close the underlying HTTP client. Not needed when used as an async context manager."""
        if self._owns_client:
            await self._http.aclose()
