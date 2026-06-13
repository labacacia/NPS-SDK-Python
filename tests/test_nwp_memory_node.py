# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for MemoryNodeServer, cgn_limit enforcement, and RFC-0005 reputation gate."""

from __future__ import annotations

import json
import asyncio
import pytest
import dataclasses

from nps_sdk.nwp.memory_node_server import (
    MemoryNodeField,
    MemoryNodeOptions,
    MemoryNodeQueryResult,
    MemoryNodeSchema,
    MemoryNodeServer,
    IMemoryNodeProvider,
    NodeRequest,
    _effective_budget,
    _measure_rows,
    _trim_to_budget,
)
from nps_sdk.nwp.frames import QueryFrame
from nps_sdk.nwp.reputation import (
    ReputationDecision,
    ReputationPolicy,
    ReputationRule,
    RepOutcome,
    IReputationEvaluator,
    DefaultReputationEvaluator,
    default_reputation_evaluator,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SCHEMA = MemoryNodeSchema(fields=[
    MemoryNodeField(name="id",   type="string"),
    MemoryNodeField(name="name", type="string"),
])

_OPTS = MemoryNodeOptions(
    node_id="urn:nps:node:test.local:memory",
    schema=_SCHEMA,
    path_prefix="/mem",
)

_ROWS = [{"id": str(i), "name": f"item-{i}"} for i in range(10)]


class _FixedProvider(IMemoryNodeProvider):
    def __init__(self, rows=None):
        self._rows = rows if rows is not None else _ROWS

    async def query(self, frame: QueryFrame, opts: MemoryNodeOptions) -> MemoryNodeQueryResult:
        return MemoryNodeQueryResult(rows=self._rows)


def _make_req(method="POST", sub="/query", body=b"{}", headers=None) -> NodeRequest:
    h = {"content-type": "application/json"}
    if headers:
        h.update(headers)
    return NodeRequest(method=method, sub_path=sub, headers=h, body=body)


def _run(coro):
    # asyncio.run() per call — get_event_loop().run_until_complete fails once another
    # async test has closed the shared loop (pytest-asyncio auto mode).
    return asyncio.run(coro)


# ── NWM endpoint ──────────────────────────────────────────────────────────────

def test_nwm_returns_json():
    server = MemoryNodeServer(_FixedProvider(), _OPTS)
    resp = _run(server.handle(_make_req("GET", "/.nwm")))
    assert resp.status == 200
    nwm = json.loads(resp.body)
    assert nwm["node_type"] == "memory"
    assert "endpoints" in nwm


def test_nwm_publishes_cgn_limit():
    opts = dataclasses.replace(_OPTS, cgn_limit=1000)
    server = MemoryNodeServer(_FixedProvider(), opts)
    resp = _run(server.handle(_make_req("GET", "/.nwm")))
    nwm = json.loads(resp.body)
    assert nwm["token_budget"]["cgn_limit"] == 1000

def test_nwm_no_token_budget_when_limit_zero():
    server = MemoryNodeServer(_FixedProvider(), _OPTS)
    resp = _run(server.handle(_make_req("GET", "/.nwm")))
    nwm = json.loads(resp.body)
    assert "token_budget" not in nwm


# ── Schema endpoint ───────────────────────────────────────────────────────────

def test_schema_returns_json():
    server = MemoryNodeServer(_FixedProvider(), _OPTS)
    resp = _run(server.handle(_make_req("GET", "/.schema")))
    assert resp.status == 200
    schema = json.loads(resp.body)
    assert "fields" in schema


# ── Query endpoint ────────────────────────────────────────────────────────────

def test_query_returns_rows():
    server = MemoryNodeServer(_FixedProvider(), _OPTS)
    resp = _run(server.handle(_make_req()))
    assert resp.status == 200
    caps = json.loads(resp.body)
    assert caps["count"] == len(_ROWS)
    assert caps["frame"] == "0x04"


def test_query_rejects_invalid_json():
    server = MemoryNodeServer(_FixedProvider(), _OPTS)
    resp = _run(server.handle(_make_req(body=b"not-json")))
    assert resp.status == 400


# ── cgn_limit enforcement ─────────────────────────────────────────────────────

def test_effective_budget_no_limit():
    assert _effective_budget(500, 0) == 500

def test_effective_budget_no_agent_budget():
    assert _effective_budget(0, 300) == 300

def test_effective_budget_clamps_to_limit():
    assert _effective_budget(1000, 300) == 300

def test_effective_budget_agent_below_limit():
    assert _effective_budget(100, 500) == 100


def test_query_trims_to_cgn_limit():
    big_rows = [{"id": str(i), "data": "x" * 400} for i in range(20)]

    class _BigProvider(IMemoryNodeProvider):
        async def query(self, frame, opts):
            return MemoryNodeQueryResult(rows=big_rows)

    # cgn_limit of 1 CGN should trim severely
    opts = dataclasses.replace(_OPTS, cgn_limit=1)
    server = MemoryNodeServer(_BigProvider(), opts)
    resp = _run(server.handle(_make_req()))
    caps = json.loads(resp.body)
    assert caps["count"] < len(big_rows)


def test_query_agent_budget_respected():
    opts = dataclasses.replace(_OPTS, cgn_limit=0)
    server = MemoryNodeServer(_FixedProvider([{"id": "x", "data": "y" * 1000}] * 50), opts)
    req = _make_req(headers={"x-nwp-budget": "1"})
    resp = _run(server.handle(req))
    caps = json.loads(resp.body)
    # At budget=1, at least some trimming should occur for large rows
    assert caps["count"] < 50


# ── Auth gate ─────────────────────────────────────────────────────────────────

def test_auth_required_rejects_missing_header():
    opts = dataclasses.replace(_OPTS, require_auth=True)
    server = MemoryNodeServer(_FixedProvider(), opts)
    resp = _run(server.handle(_make_req(headers={})))
    assert resp.status == 401


def test_auth_required_accepts_with_header():
    opts = dataclasses.replace(_OPTS, require_auth=True)
    server = MemoryNodeServer(_FixedProvider(), opts)
    resp = _run(server.handle(_make_req(headers={"x-nwp-agent": "urn:nps:agent:ca.test:alice"})))
    assert resp.status == 200


# ── Stream endpoint ───────────────────────────────────────────────────────────

def test_stream_returns_ndjson():
    server = MemoryNodeServer(_FixedProvider(), _OPTS)
    resp = _run(server.handle(_make_req("POST", "/stream")))
    assert resp.status == 200
    lines = [l for l in resp.body.decode().strip().split("\n") if l]
    assert len(lines) == 2
    last = json.loads(lines[-1])
    assert last["is_last"] is True


# ── Method not allowed ────────────────────────────────────────────────────────

def test_query_get_method_not_allowed():
    server = MemoryNodeServer(_FixedProvider(), _OPTS)
    resp = _run(server.handle(_make_req("GET", "/query")))
    assert resp.status == 405


# ── Reputation gate ───────────────────────────────────────────────────────────

class _StubEvaluator(IReputationEvaluator):
    def __init__(self, outcome: RepOutcome):
        self._outcome = outcome

    async def evaluate(self, nid, assurance_level, policy):
        return ReputationDecision(outcome=self._outcome)

    def clear_ban(self, nid): pass


def test_reputation_ban_returns_403():
    policy = ReputationPolicy()
    opts = dataclasses.replace(_OPTS, reputation_policy=policy)
    server = MemoryNodeServer(_FixedProvider(), opts,
                               evaluator=_StubEvaluator(RepOutcome.BAN))
    req = _make_req(headers={"x-nwp-agent": "urn:nps:agent:ca.test:malicious"})
    resp = _run(server.handle(req))
    assert resp.status == 403


def test_reputation_reject_returns_403():
    policy = ReputationPolicy()
    opts = dataclasses.replace(_OPTS, reputation_policy=policy)
    server = MemoryNodeServer(_FixedProvider(), opts,
                               evaluator=_StubEvaluator(RepOutcome.REJECT))
    req = _make_req(headers={"x-nwp-agent": "urn:nps:agent:ca.test:bad"})
    resp = _run(server.handle(req))
    assert resp.status == 403


def test_reputation_throttle_returns_429():
    policy = ReputationPolicy()
    opts = dataclasses.replace(_OPTS, reputation_policy=policy)
    server = MemoryNodeServer(_FixedProvider(), opts,
                               evaluator=_StubEvaluator(RepOutcome.THROTTLE))
    req = _make_req(headers={"x-nwp-agent": "urn:nps:agent:ca.test:noisy"})
    resp = _run(server.handle(req))
    assert resp.status == 429


def test_reputation_accept_passes_through():
    policy = ReputationPolicy()
    opts = dataclasses.replace(_OPTS, reputation_policy=policy)
    server = MemoryNodeServer(_FixedProvider(), opts,
                               evaluator=_StubEvaluator(RepOutcome.ACCEPT))
    req = _make_req(headers={"x-nwp-agent": "urn:nps:agent:ca.test:good"})
    resp = _run(server.handle(req))
    assert resp.status == 200


def test_reputation_skipped_without_agent_nid():
    policy = ReputationPolicy()
    opts = dataclasses.replace(_OPTS, reputation_policy=policy)
    server = MemoryNodeServer(_FixedProvider(), opts,
                               evaluator=_StubEvaluator(RepOutcome.BAN))
    # No x-nwp-agent header — reputation gate should be skipped
    resp = _run(server.handle(_make_req(headers={})))
    assert resp.status == 200


# ── DefaultReputationEvaluator unit tests ─────────────────────────────────────

def test_default_evaluator_accept_when_disabled():
    ev = DefaultReputationEvaluator()
    policy = ReputationPolicy(enabled=False)
    d = _run(ev.evaluate("urn:nps:agent:ca:x", "anonymous", policy))
    assert d.outcome == RepOutcome.ACCEPT


def test_default_evaluator_assurance_floor():
    ev = DefaultReputationEvaluator()
    policy = ReputationPolicy(min_assurance_level="verified")
    d = _run(ev.evaluate("urn:nps:agent:ca:x", "anonymous", policy))
    assert d.outcome == RepOutcome.REJECT
    assert d.error_code == "NWP-AUTH-ASSURANCE-TOO-LOW"


def test_default_evaluator_ban_cache():
    import time
    ev = DefaultReputationEvaluator()
    # Inject a ban manually
    nid = "urn:nps:agent:ca:bananable"
    ev._bans[nid] = time.monotonic() + 3600
    policy = ReputationPolicy()
    d = _run(ev.evaluate(nid, "anonymous", policy))
    assert d.outcome == RepOutcome.BAN

    ev.clear_ban(nid)
    d2 = _run(ev.evaluate(nid, "anonymous", policy))
    assert d2.outcome == RepOutcome.ACCEPT


def test_default_evaluator_singleton():
    a = default_reputation_evaluator()
    b = default_reputation_evaluator()
    assert a is b


def test_default_evaluator_no_sources_allow():
    ev = DefaultReputationEvaluator()
    policy = ReputationPolicy(
        log_sources=[],
        on_log_unavailable="allow",
        ban_on=[ReputationRule(incident="*", severity=">=info", count=1)],
    )
    d = _run(ev.evaluate("urn:nps:agent:ca:x", "anonymous", policy))
    # No log sources → empty entries → no rule fires → accept
    assert d.outcome == RepOutcome.ACCEPT


def test_default_evaluator_no_sources_deny():
    ev = DefaultReputationEvaluator()
    policy = ReputationPolicy(
        log_sources=[],
        on_log_unavailable="deny",
        ban_on=[ReputationRule(incident="*", severity=">=info", count=1)],
    )
    d = _run(ev.evaluate("urn:nps:agent:ca:x2", "anonymous", policy))
    # No sources + deny → synthetic critical entry → ban_on fires
    assert d.outcome == RepOutcome.BAN
