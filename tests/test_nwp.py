# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NWP frame dataclasses."""

import pytest

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.nwp.frames import (
    ActionFrame,
    AsyncActionResponse,
    QueryFrame,
    QueryOrderClause,
    VectorSearchOptions,
)


@pytest.fixture
def full_registry() -> FrameRegistry:
    return FrameRegistry.create_full()


@pytest.fixture
def codec(full_registry: FrameRegistry) -> NpsFrameCodec:
    return NpsFrameCodec(full_registry)


# ── QueryFrame ───────────────────────────────────────────────────────────────

class TestQueryFrame:
    def test_minimal_roundtrip_json(self, codec: NpsFrameCodec):
        frame = QueryFrame(limit=10)
        wire  = codec.encode(frame, override_tier=EncodingTier.JSON)
        out   = codec.decode(wire)
        assert isinstance(out, QueryFrame)
        assert out.limit      == 10
        assert out.anchor_ref is None
        assert out.cursor     is None

    def test_full_roundtrip_msgpack(self, codec: NpsFrameCodec):
        frame = QueryFrame(
            anchor_ref="sha256:" + "a" * 64,
            filter={"$eq": {"status": "active"}},
            fields=("id", "price"),
            limit=50,
            cursor="tok123",
            order=(QueryOrderClause(field="price", dir="DESC"),),
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, QueryFrame)
        assert out.anchor_ref == frame.anchor_ref
        assert out.fields     == ("id", "price")
        assert out.limit      == 50
        assert out.cursor     == "tok123"
        assert out.order      is not None
        assert out.order[0].field == "price"
        assert out.order[0].dir   == "DESC"

    def test_frame_type(self):
        assert QueryFrame().frame_type == FrameType.QUERY

    def test_vector_search_roundtrip(self, codec: NpsFrameCodec):
        vs    = VectorSearchOptions(field="emb", vector=(0.1, 0.2, 0.3), top_k=5, metric="cosine")
        frame = QueryFrame(vector_search=vs)
        out   = codec.decode(codec.encode(frame))
        assert out.vector_search is not None
        assert out.vector_search.field  == "emb"
        assert out.vector_search.top_k  == 5
        assert out.vector_search.metric == "cosine"
        assert len(out.vector_search.vector) == 3


# ── ActionFrame ──────────────────────────────────────────────────────────────

class TestActionFrame:
    def test_minimal_roundtrip(self, codec: NpsFrameCodec):
        frame = ActionFrame(action_id="orders.create")
        out   = codec.decode(codec.encode(frame))
        assert isinstance(out, ActionFrame)
        assert out.action_id  == "orders.create"
        assert out.params     is None
        assert out.async_     is False
        assert out.timeout_ms == 5000

    def test_full_roundtrip(self, codec: NpsFrameCodec):
        frame = ActionFrame(
            action_id="inventory.restock",
            params={"sku": "X-101", "qty": 50},
            idempotency_key="idem-abc-123",
            timeout_ms=30_000,
            async_=True,
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, ActionFrame)
        assert out.idempotency_key == "idem-abc-123"
        assert out.timeout_ms      == 30_000
        assert out.async_          is True

    def test_frame_type(self):
        assert ActionFrame(action_id="x").frame_type == FrameType.ACTION

    def test_preferred_tier(self):
        assert ActionFrame(action_id="x").preferred_tier == EncodingTier.MSGPACK


# ── VectorSearchOptions ───────────────────────────────────────────────────────

class TestVectorSearchOptions:
    def test_roundtrip_dict(self):
        vs  = VectorSearchOptions(field="emb", vector=(1.0, 2.0), top_k=3, threshold=0.8)
        out = VectorSearchOptions.from_dict(vs.to_dict())
        assert out.field     == "emb"
        assert out.top_k     == 3
        assert out.threshold == 0.8

    def test_defaults(self):
        vs = VectorSearchOptions.from_dict({"field": "v", "vector": [0.0]})
        assert vs.top_k  == 10
        assert vs.metric == "cosine"
        assert vs.threshold is None


# ── QueryOrderClause ──────────────────────────────────────────────────────────

class TestQueryOrderClause:
    def test_roundtrip(self):
        oc  = QueryOrderClause(field="created_at", dir="ASC")
        out = QueryOrderClause.from_dict(oc.to_dict())
        assert out.field == "created_at"
        assert out.dir   == "ASC"


# ── AsyncActionResponse ───────────────────────────────────────────────────────

class TestAsyncActionResponse:
    def test_roundtrip(self):
        r   = AsyncActionResponse(task_id="t-1", status="pending", poll_url="nwp://host/tasks/t-1")
        out = AsyncActionResponse.from_dict(r.to_dict())
        assert out.task_id  == "t-1"
        assert out.status   == "pending"
        assert out.poll_url == "nwp://host/tasks/t-1"
