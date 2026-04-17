# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NwpClient using respx to mock httpx."""

import json
import pytest
import httpx
import respx

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import AnchorFrame, CapsFrame, FrameSchema, SchemaField, StreamFrame
from nps_sdk.nwp.client import NwpClient
from nps_sdk.nwp.frames import ActionFrame, AsyncActionResponse, QueryFrame


BASE_URL = "http://node.example.com"


@pytest.fixture
def full_registry() -> FrameRegistry:
    return FrameRegistry.create_full()


@pytest.fixture
def server_codec(full_registry: FrameRegistry) -> NpsFrameCodec:
    """Codec used to build mock server responses."""
    return NpsFrameCodec(full_registry)


@pytest.fixture
def schema() -> FrameSchema:
    return FrameSchema(fields=(
        SchemaField(name="id",   type="uint64"),
        SchemaField(name="name", type="string"),
    ))


@pytest.fixture
def anchor_id(schema: FrameSchema) -> str:
    from nps_sdk.core.cache import AnchorFrameCache
    return AnchorFrameCache.compute_anchor_id(schema)


# ── send_anchor ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
@respx.mock
async def test_send_anchor(schema: FrameSchema, anchor_id: str):
    frame = AnchorFrame(anchor_id=anchor_id, schema=schema)
    respx.post(f"{BASE_URL}/anchor").mock(return_value=httpx.Response(204))

    async with NwpClient(BASE_URL) as client:
        await client.send_anchor(frame)  # should not raise


# ── query ─────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
@respx.mock
async def test_query_returns_caps_frame(
    schema: FrameSchema,
    anchor_id: str,
    server_codec: NpsFrameCodec,
):
    response_frame = CapsFrame(
        anchor_ref=anchor_id,
        count=2,
        data=({"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}),
    )
    wire = server_codec.encode(response_frame, override_tier=EncodingTier.MSGPACK)

    respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(200, content=wire, headers={"content-type": "application/x-nps-frame"})
    )

    async with NwpClient(BASE_URL) as client:
        result = await client.query(QueryFrame(anchor_ref=anchor_id, limit=10))

    assert isinstance(result, CapsFrame)
    assert result.count      == 2
    assert result.anchor_ref == anchor_id


@pytest.mark.anyio
@respx.mock
async def test_query_wrong_frame_type_raises(
    schema: FrameSchema,
    anchor_id: str,
    server_codec: NpsFrameCodec,
):
    # Server returns an ErrorFrame instead of CapsFrame
    from nps_sdk.ncp.frames import ErrorFrame
    error_frame = ErrorFrame(status="NPS-SERVER-INTERNAL", error="NWP-NODE-UNAVAILABLE")
    wire = server_codec.encode(error_frame, override_tier=EncodingTier.MSGPACK)

    respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(200, content=wire, headers={"content-type": "application/x-nps-frame"})
    )

    async with NwpClient(BASE_URL) as client:
        with pytest.raises(TypeError):
            await client.query(QueryFrame(limit=5))


# ── invoke ────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
@respx.mock
async def test_invoke_sync_json_response():
    result_data = {"order_id": "ORD-9001", "status": "created"}
    respx.post(f"{BASE_URL}/invoke").mock(
        return_value=httpx.Response(
            200,
            content=json.dumps(result_data).encode(),
            headers={"content-type": "application/json"},
        )
    )

    async with NwpClient(BASE_URL) as client:
        result = await client.invoke(ActionFrame(action_id="orders.create"))

    assert result["order_id"] == "ORD-9001"


@pytest.mark.anyio
@respx.mock
async def test_invoke_async_returns_async_response():
    async_resp = {"task_id": "task-123", "status": "pending", "poll_url": f"{BASE_URL}/tasks/task-123"}
    respx.post(f"{BASE_URL}/invoke").mock(
        return_value=httpx.Response(
            202,
            content=json.dumps(async_resp).encode(),
            headers={"content-type": "application/json"},
        )
    )

    async with NwpClient(BASE_URL) as client:
        result = await client.invoke(ActionFrame(action_id="jobs.process", async_=True))

    assert isinstance(result, AsyncActionResponse)
    assert result.task_id == "task-123"
    assert result.status  == "pending"


# ── stream ────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
@respx.mock
async def test_stream_yields_chunks(
    anchor_id: str,
    server_codec: NpsFrameCodec,
):
    chunk1 = StreamFrame(stream_id="s-1", seq=0, is_last=False, data=({"id": 1},), anchor_ref=anchor_id)
    chunk2 = StreamFrame(stream_id="s-1", seq=1, is_last=True,  data=({"id": 2},))
    wire1  = server_codec.encode(chunk1)
    wire2  = server_codec.encode(chunk2)

    async def iter_chunks():
        yield wire1
        yield wire2

    respx.post(f"{BASE_URL}/stream").mock(
        return_value=httpx.Response(200, content=wire1 + wire2, headers={"content-type": "application/x-nps-frame"})
    )

    chunks: list[StreamFrame] = []
    async with NwpClient(BASE_URL) as client:
        async for chunk in client.stream(QueryFrame(anchor_ref=anchor_id)):
            chunks.append(chunk)

    # At minimum we get the first chunk that was in the response
    assert len(chunks) >= 1
    assert isinstance(chunks[0], StreamFrame)


# ── context manager / close ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_external_http_client_not_closed():
    """When NwpClient is given an external httpx.AsyncClient, it does not close it on exit."""
    http = httpx.AsyncClient()
    client = NwpClient(BASE_URL, http_client=http)
    async with client:
        pass
    assert not http.is_closed
    await http.aclose()


@pytest.mark.anyio
async def test_close_explicit():
    client = NwpClient(BASE_URL)
    await client.close()  # should not raise


@pytest.mark.anyio
@respx.mock
async def test_http_error_propagates():
    respx.post(f"{BASE_URL}/query").mock(return_value=httpx.Response(503))
    async with NwpClient(BASE_URL) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.query(QueryFrame())


@pytest.mark.anyio
@respx.mock
async def test_invoke_sync_nps_frame_response(
    anchor_id: str,
    server_codec: NpsFrameCodec,
):
    """invoke() should decode and return an NPS frame when content-type is application/x-nps-frame."""
    response_frame = CapsFrame(
        anchor_ref=anchor_id,
        count=1,
        data=({"result": "ok"},),
    )
    wire = server_codec.encode(response_frame, override_tier=EncodingTier.MSGPACK)

    respx.post(f"{BASE_URL}/invoke").mock(
        return_value=httpx.Response(
            200,
            content=wire,
            headers={"content-type": "application/x-nps-frame"},
        )
    )

    async with NwpClient(BASE_URL) as client:
        result = await client.invoke(ActionFrame(action_id="action.run"))

    assert isinstance(result, CapsFrame)
    assert result.count == 1


@pytest.mark.anyio
@respx.mock
async def test_stream_non_stream_frame_raises(
    anchor_id: str,
    server_codec: NpsFrameCodec,
):
    """stream() must raise TypeError if a non-StreamFrame chunk arrives."""
    # Return a CapsFrame where the client expects a StreamFrame
    caps_wire = server_codec.encode(
        CapsFrame(anchor_ref=anchor_id, count=0, data=()),
        override_tier=EncodingTier.MSGPACK,
    )

    respx.post(f"{BASE_URL}/stream").mock(
        return_value=httpx.Response(
            200,
            content=caps_wire,
            headers={"content-type": "application/x-nps-frame"},
        )
    )

    async with NwpClient(BASE_URL) as client:
        with pytest.raises(TypeError):
            async for _ in client.stream(QueryFrame(anchor_ref=anchor_id)):
                pass
