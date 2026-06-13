# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for RFC-0005 reputation policy types and DefaultReputationEvaluator."""

from __future__ import annotations

import asyncio
import time

import pytest

from nps_sdk.nwp.reputation import (
    ReputationDecision,
    ReputationPolicy,
    ReputationRule,
    RepOutcome,
    IReputationEvaluator,
    DefaultReputationEvaluator,
    default_reputation_evaluator,
)


def _run(coro):
    # asyncio.run() creates a fresh event loop per call; the previous
    # get_event_loop().run_until_complete() pattern fails once another async
    # test (pytest-asyncio auto mode) has closed the shared loop.
    return asyncio.run(coro)


# ── Policy type defaults ──────────────────────────────────────────────────────

def test_reputation_policy_defaults():
    p = ReputationPolicy()
    assert p.enabled is True
    assert p.min_assurance_level == "anonymous"
    assert p.cache_ttl_seconds == 300
    assert p.ban_ttl_seconds == 3600
    assert p.on_log_unavailable == "allow"
    assert p.log_sources == []
    assert p.ban_on == []
    assert p.reject_on == []
    assert p.throttle_on == []


def test_reputation_rule_defaults():
    r = ReputationRule(incident="abuse", severity=">=minor")
    assert r.incident == "abuse"
    assert r.severity == ">=minor"
    assert r.within_days is None
    assert r.count == 1


def test_rep_outcome_variants_distinct():
    assert RepOutcome.ACCEPT   != RepOutcome.THROTTLE
    assert RepOutcome.ACCEPT   != RepOutcome.REJECT
    assert RepOutcome.ACCEPT   != RepOutcome.BAN
    assert RepOutcome.THROTTLE != RepOutcome.REJECT
    assert RepOutcome.THROTTLE != RepOutcome.BAN
    assert RepOutcome.REJECT   != RepOutcome.BAN


def test_reputation_decision_is_accepted_true():
    d = ReputationDecision(outcome=RepOutcome.ACCEPT)
    # ReputationDecision is a dataclass; no is_accepted() helper in Python —
    # test the outcome attribute directly
    assert d.outcome == RepOutcome.ACCEPT


def test_reputation_decision_non_accept_outcomes():
    for outcome in (RepOutcome.REJECT, RepOutcome.BAN, RepOutcome.THROTTLE):
        d = ReputationDecision(outcome=outcome)
        assert d.outcome != RepOutcome.ACCEPT


# ── DefaultReputationEvaluator ────────────────────────────────────────────────

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
    ev = DefaultReputationEvaluator()
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
    assert d.outcome == RepOutcome.ACCEPT


def test_default_evaluator_no_sources_deny():
    ev = DefaultReputationEvaluator()
    policy = ReputationPolicy(
        log_sources=[],
        on_log_unavailable="deny",
        ban_on=[ReputationRule(incident="*", severity=">=info", count=1)],
    )
    d = _run(ev.evaluate("urn:nps:agent:ca:x2", "anonymous", policy))
    assert d.outcome == RepOutcome.BAN
