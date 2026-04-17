# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for AnchorFrameCache."""

import time
import pytest

from nps_sdk.core.cache import AnchorFrameCache
from nps_sdk.core.exceptions import NpsAnchorNotFoundError, NpsAnchorPoisonError
from nps_sdk.ncp.frames import AnchorFrame, FrameSchema, SchemaField


@pytest.fixture
def schema_a() -> FrameSchema:
    return FrameSchema(fields=(
        SchemaField(name="id",   type="uint64"),
        SchemaField(name="name", type="string"),
    ))


@pytest.fixture
def schema_b() -> FrameSchema:
    return FrameSchema(fields=(
        SchemaField(name="id",    type="uint64"),
        SchemaField(name="price", type="decimal"),
    ))


@pytest.fixture
def cache() -> AnchorFrameCache:
    return AnchorFrameCache()


class TestAnchorId:
    def test_compute_is_deterministic(self, schema_a: FrameSchema):
        id1 = AnchorFrameCache.compute_anchor_id(schema_a)
        id2 = AnchorFrameCache.compute_anchor_id(schema_a)
        assert id1 == id2

    def test_starts_with_sha256_prefix(self, schema_a: FrameSchema):
        aid = AnchorFrameCache.compute_anchor_id(schema_a)
        assert aid.startswith("sha256:")
        assert len(aid) == len("sha256:") + 64

    def test_different_schemas_produce_different_ids(self, schema_a: FrameSchema, schema_b: FrameSchema):
        assert AnchorFrameCache.compute_anchor_id(schema_a) != AnchorFrameCache.compute_anchor_id(schema_b)

    def test_field_order_independent(self):
        s1 = FrameSchema(fields=(
            SchemaField(name="a", type="string"),
            SchemaField(name="b", type="uint64"),
        ))
        s2 = FrameSchema(fields=(
            SchemaField(name="b", type="uint64"),
            SchemaField(name="a", type="string"),
        ))
        # Fields are sorted before hashing → same anchor_id
        assert AnchorFrameCache.compute_anchor_id(s1) == AnchorFrameCache.compute_anchor_id(s2)


class TestAnchorFrameCacheSet:
    def test_set_returns_anchor_id(self, cache: AnchorFrameCache, schema_a: FrameSchema):
        frame = AnchorFrame(
            anchor_id=AnchorFrameCache.compute_anchor_id(schema_a),
            schema=schema_a,
        )
        aid = cache.set(frame)
        assert aid.startswith("sha256:")

    def test_set_and_get(self, cache: AnchorFrameCache, schema_a: FrameSchema):
        aid   = AnchorFrameCache.compute_anchor_id(schema_a)
        frame = AnchorFrame(anchor_id=aid, schema=schema_a)
        cache.set(frame)
        result = cache.get(aid)
        assert result is not None
        assert result.anchor_id == aid

    def test_idempotent_set(self, cache: AnchorFrameCache, schema_a: FrameSchema):
        aid    = AnchorFrameCache.compute_anchor_id(schema_a)
        frame  = AnchorFrame(anchor_id=aid, schema=schema_a)
        id1    = cache.set(frame)
        id2    = cache.set(frame)  # same frame again
        assert id1 == id2
        assert len(cache) == 1

    def test_anchor_poison_raises(
        self,
        cache: AnchorFrameCache,
        schema_a: FrameSchema,
        schema_b: FrameSchema,
    ):
        aid   = AnchorFrameCache.compute_anchor_id(schema_a)
        frame = AnchorFrame(anchor_id=aid, schema=schema_a)
        cache.set(frame)

        # Same anchor_id but different schema → poison
        poison = AnchorFrame(anchor_id=aid, schema=schema_b)
        with pytest.raises(NpsAnchorPoisonError) as exc_info:
            cache.set(poison)
        assert exc_info.value.anchor_id == aid

    def test_set_without_sha256_prefix_computes_id(
        self, cache: AnchorFrameCache, schema_a: FrameSchema
    ):
        frame = AnchorFrame(anchor_id="raw-id-no-prefix", schema=schema_a)
        aid   = cache.set(frame)
        # Should have computed the real anchor_id
        assert aid.startswith("sha256:")


class TestAnchorFrameCacheGet:
    def test_get_returns_none_when_missing(self, cache: AnchorFrameCache):
        assert cache.get("sha256:" + "0" * 64) is None

    def test_get_required_returns_frame_when_present(
        self, cache: AnchorFrameCache, schema_a: FrameSchema
    ):
        aid   = AnchorFrameCache.compute_anchor_id(schema_a)
        frame = AnchorFrame(anchor_id=aid, schema=schema_a)
        cache.set(frame)
        result = cache.get_required(aid)
        assert result is not None
        assert result.anchor_id == aid

    def test_get_required_raises_when_missing(self, cache: AnchorFrameCache):
        with pytest.raises(NpsAnchorNotFoundError):
            cache.get_required("sha256:" + "0" * 64)

    def test_get_after_ttl_expiry(self, cache: AnchorFrameCache, schema_a: FrameSchema):
        aid   = AnchorFrameCache.compute_anchor_id(schema_a)
        frame = AnchorFrame(anchor_id=aid, schema=schema_a, ttl=0)
        cache.set(frame)
        # TTL=0 → expires immediately
        time.sleep(0.01)
        assert cache.get(aid) is None


class TestAnchorFrameCacheInvalidate:
    def test_invalidate(self, cache: AnchorFrameCache, schema_a: FrameSchema):
        aid   = AnchorFrameCache.compute_anchor_id(schema_a)
        frame = AnchorFrame(anchor_id=aid, schema=schema_a)
        cache.set(frame)
        cache.invalidate(aid)
        assert cache.get(aid) is None

    def test_invalidate_noop_when_missing(self, cache: AnchorFrameCache):
        cache.invalidate("sha256:" + "9" * 64)  # should not raise


class TestAnchorFrameCacheLen:
    def test_len(self, cache: AnchorFrameCache, schema_a: FrameSchema, schema_b: FrameSchema):
        f1 = AnchorFrame(anchor_id=AnchorFrameCache.compute_anchor_id(schema_a), schema=schema_a)
        f2 = AnchorFrame(anchor_id=AnchorFrameCache.compute_anchor_id(schema_b), schema=schema_b)
        cache.set(f1)
        cache.set(f2)
        assert len(cache) == 2

    def test_len_evicts_expired_entries(
        self, cache: AnchorFrameCache, schema_a: FrameSchema, schema_b: FrameSchema
    ):
        f1 = AnchorFrame(anchor_id=AnchorFrameCache.compute_anchor_id(schema_a), schema=schema_a, ttl=3600)
        f2 = AnchorFrame(anchor_id=AnchorFrameCache.compute_anchor_id(schema_b), schema=schema_b, ttl=0)
        cache.set(f1)
        cache.set(f2)
        # f2 expires immediately; len() triggers _evict_expired() → del self._store[k]
        time.sleep(0.01)
        assert len(cache) == 1
