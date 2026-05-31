# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for SubscribeFrame (NWP v0.13)."""

import pytest
from nps_sdk.nwp.frames import SubscribeFrame


def test_construction_minimal():
    """Construction with just subscription_id works."""
    f = SubscribeFrame(subscription_id="sub-abc")
    assert f.subscription_id == "sub-abc"
    assert f.filter is None
    assert f.heartbeat_interval_ms is None
    assert f.max_events is None
    assert f.cursor is None


def test_to_dict_only_non_none():
    """to_dict() only includes non-None optional fields."""
    f = SubscribeFrame(subscription_id="sub-1")
    d = f.to_dict()
    assert d == {"subscription_id": "sub-1"}
    assert "filter" not in d
    assert "heartbeat_interval_ms" not in d
    assert "max_events" not in d
    assert "cursor" not in d


def test_from_dict_round_trip():
    """from_dict() round-trip preserves all fields."""
    original = SubscribeFrame(
        subscription_id="sub-xyz",
        filter={"event_type": "node.joined"},
        heartbeat_interval_ms=5000,
        max_events=100,
        cursor="cursor-token-42",
    )
    d = original.to_dict()
    restored = SubscribeFrame.from_dict(d)
    assert restored == original


def test_optional_fields_preserved():
    """filter, heartbeat_interval_ms, max_events, and cursor are preserved in to_dict/from_dict."""
    f = SubscribeFrame(
        subscription_id="sub-full",
        filter={"event_type": "node.updated", "node_id": "n-1"},
        heartbeat_interval_ms=30000,
        max_events=500,
        cursor="tok-99",
    )
    d = f.to_dict()
    assert d["filter"] == {"event_type": "node.updated", "node_id": "n-1"}
    assert d["heartbeat_interval_ms"] == 30000
    assert d["max_events"] == 500
    assert d["cursor"] == "tok-99"

    restored = SubscribeFrame.from_dict(d)
    assert restored.filter == f.filter
    assert restored.heartbeat_interval_ms == f.heartbeat_interval_ms
    assert restored.max_events == f.max_events
    assert restored.cursor == f.cursor


def test_from_dict_minimal():
    """from_dict() with only subscription_id sets optionals to None."""
    f = SubscribeFrame.from_dict({"subscription_id": "sub-min"})
    assert f.subscription_id == "sub-min"
    assert f.filter is None
    assert f.heartbeat_interval_ms is None
    assert f.max_events is None
    assert f.cursor is None
