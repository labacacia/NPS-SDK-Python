# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Telemetry primitive + instrumentation wiring tests."""

from __future__ import annotations

import asyncio

import pytest

from nps_sdk.core.telemetry import Counter, Histogram, Meter, Tracer


# ── Core primitives ─────────────────────────────────────────────────────────

def test_counter_accumulates_per_labelset():
    c = Counter("nps.frames.processed", "{frames}", "desc")
    c.add(1, frame_type="query", result="success")
    c.add(2, frame_type="query", result="success")
    c.add(1, frame_type="action", result="success")
    snap = c.snapshot()
    assert snap.value(frame_type="query", result="success") == 3
    assert snap.value(frame_type="action", result="success") == 1
    assert snap.total() == 4


def test_counter_no_labels():
    c = Counter("x")
    c.add()
    c.add(5)
    assert c.snapshot().total() == 6


def test_histogram_records_samples():
    h = Histogram("nps.frames.processing_ms", "ms")
    h.record(1.5, frame_type="query")
    h.record(2.5, frame_type="query")
    snap = h.snapshot()
    assert snap.count(frame_type="query") == 2
    assert snap.sum(frame_type="query") == 4.0
    assert snap.samples(frame_type="query") == [1.5, 2.5]
    assert snap.count() == 2


def test_meter_creates_and_resets():
    m = Meter("nps.nwp", "1.0.0")
    c = m.create_counter("c")
    h = m.create_histogram("h")
    c.add(3)
    h.record(9)
    m.reset()
    assert c.snapshot().total() == 0
    assert h.snapshot().count() == 0


def test_tracer_records_span_with_status_and_attributes():
    tr = Tracer("nps.nwp")
    with tr.start_span("op", key="v") as span:
        span.set_attribute("extra", 1)
    spans = tr.recorded_spans()
    assert len(spans) == 1
    assert spans[0].name == "op"
    assert spans[0].attributes == {"key": "v", "extra": 1}
    assert spans[0].status == "ok"
    assert spans[0].end_ns is not None
    assert spans[0].duration_ms >= 0


def test_tracer_marks_error_on_exception():
    tr = Tracer("nps.nwp")
    with pytest.raises(ValueError):
        with tr.start_span("op") as span:
            raise ValueError("boom")
    assert tr.recorded_spans()[0].status == "error"


# ── NWP instrumentation names match .NET ────────────────────────────────────

def test_nwp_instrument_names():
    from nps_sdk.nwp import instrumentation as nwp
    assert nwp.ACTIVITY_SOURCE_NAME == "nps.nwp"
    assert nwp.frames_processed.name == "nps.frames.processed"
    assert nwp.frame_duration_ms.name == "nps.frames.processing_ms"
    assert nwp.cgn_consumed.name == "nps.cgn.consumed"
    assert nwp.frame_errors.name == "nps.frames.errors"


def test_nop_instrument_names():
    from nps_sdk.nop import instrumentation as nop
    assert nop.ACTIVITY_SOURCE_NAME == "nps.nop"
    assert nop.task_duration_ms.name == "nps.nop.task.duration_ms"
    assert nop.node_duration_ms.name == "nps.nop.node.duration_ms"
    assert nop.node_retries.name == "nps.nop.node.retries"
    assert nop.tasks_completed.name == "nps.nop.tasks.completed"
    assert nop.tasks_failed.name == "nps.nop.tasks.failed"


# ── Memory node server records NWP metrics + span on query ───────────────────

def test_memory_node_query_records_telemetry():
    import json

    from nps_sdk.nwp import instrumentation as nwp
    from nps_sdk.nwp.memory_node_server import (
        InMemoryMemoryNodeProvider,
        MemoryNodeField,
        MemoryNodeOptions,
        MemoryNodeSchema,
        MemoryNodeServer,
        NodeRequest,
    )

    nwp.reset()
    schema = MemoryNodeSchema(fields=[MemoryNodeField("id", "number")])
    provider = InMemoryMemoryNodeProvider([{"id": 1}, {"id": 2}])
    server = MemoryNodeServer(provider, MemoryNodeOptions(node_id="n", schema=schema))

    req = NodeRequest("POST", "/query", {}, json.dumps({"limit": 10}).encode())
    resp = asyncio.run(server.handle(req))
    assert resp.status == 200

    assert nwp.frames_processed.snapshot().value(
        frame_type="query", result="success") == 1
    assert nwp.frame_duration_ms.snapshot().count(frame_type="query") == 1
    spans = [s for s in nwp.source.recorded_spans() if s.name == "nps.nwp.query"]
    assert len(spans) == 1


def test_memory_node_bad_filter_records_error_counter():
    from nps_sdk.nwp import instrumentation as nwp
    from nps_sdk.nwp.memory_node_server import (
        InMemoryMemoryNodeProvider,
        MemoryNodeField,
        MemoryNodeOptions,
        MemoryNodeSchema,
        MemoryNodeServer,
        NodeRequest,
    )

    nwp.reset()
    schema = MemoryNodeSchema(fields=[MemoryNodeField("id", "number")])
    server = MemoryNodeServer(
        InMemoryMemoryNodeProvider([]), MemoryNodeOptions(node_id="n", schema=schema))
    req = NodeRequest("POST", "/query", {}, b"not-json")
    resp = asyncio.run(server.handle(req))
    assert resp.status == 400
    assert nwp.frame_errors.snapshot().value(
        frame_type="query", error_code="NWP-QUERY-FILTER-INVALID") == 1
