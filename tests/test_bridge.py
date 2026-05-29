# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NWP Bridge Node type definitions (NPS-CR-0001)."""

import pytest

from nps_sdk.nwp.bridge import (
    BridgeNodeDescriptor,
    BridgeProtocols,
    BridgeTarget,
    NODE_TYPE_BRIDGE,
)
from nps_sdk.ndp.frames import AnnounceFrame, NdpAddress


# ── BridgeProtocols ───────────────────────────────────────────────────────────

def test_bridge_protocols_http():
    assert BridgeProtocols.HTTP == "http"


def test_bridge_protocols_grpc():
    assert BridgeProtocols.GRPC == "grpc"


def test_bridge_protocols_mcp():
    assert BridgeProtocols.MCP == "mcp"


def test_bridge_protocols_a2a():
    assert BridgeProtocols.A2A == "a2a"


def test_bridge_protocols_standard_contains_all_four():
    assert "http" in BridgeProtocols.STANDARD
    assert "grpc" in BridgeProtocols.STANDARD
    assert "mcp"  in BridgeProtocols.STANDARD
    assert "a2a"  in BridgeProtocols.STANDARD
    assert len(BridgeProtocols.STANDARD) == 4


# ── NODE_TYPE_BRIDGE ──────────────────────────────────────────────────────────

def test_node_type_bridge_constant():
    assert NODE_TYPE_BRIDGE == "bridge"


# ── BridgeNodeDescriptor ──────────────────────────────────────────────────────

def test_bridge_node_descriptor_fields():
    desc = BridgeNodeDescriptor(
        nid="urn:nps:node:example.com:bridge",
        supported_protocols=frozenset({"http", "grpc"}),
    )
    assert desc.nid == "urn:nps:node:example.com:bridge"
    assert "http" in desc.supported_protocols
    assert "grpc" in desc.supported_protocols


def test_bridge_node_descriptor_to_bridge_protocols_sorted():
    desc = BridgeNodeDescriptor(
        nid="urn:nps:node:example.com:bridge",
        supported_protocols=frozenset({"mcp", "a2a", "http", "grpc"}),
    )
    result = desc.to_bridge_protocols()
    assert result == sorted(["mcp", "a2a", "http", "grpc"])


def test_bridge_node_descriptor_is_frozen():
    desc = BridgeNodeDescriptor(
        nid="urn:nps:node:example.com:bridge",
        supported_protocols=frozenset({"http"}),
    )
    with pytest.raises(Exception):
        # frozen=True dataclass raises FrozenInstanceError on attribute assignment
        desc.nid = "other"  # type: ignore[misc]


# ── BridgeTarget ──────────────────────────────────────────────────────────────

def test_bridge_target_to_dict_with_extras():
    bt = BridgeTarget(
        protocol="http",
        endpoint="https://example.com/api",
        extras={"timeout": 30, "auth": "bearer"},
    )
    d = bt.to_dict()
    assert d["protocol"] == "http"
    assert d["endpoint"] == "https://example.com/api"
    assert d["extras"] == {"timeout": 30, "auth": "bearer"}


def test_bridge_target_to_dict_without_extras():
    bt = BridgeTarget(protocol="grpc", endpoint="grpc://example.com:50051")
    d = bt.to_dict()
    assert d["protocol"] == "grpc"
    assert d["endpoint"] == "grpc://example.com:50051"
    assert "extras" not in d


def test_bridge_target_roundtrip_with_extras():
    original = BridgeTarget(
        protocol="mcp",
        endpoint="https://mcp.example.com",
        extras={"key": "value"},
    )
    restored = BridgeTarget.from_dict(original.to_dict())
    assert restored.protocol == original.protocol
    assert restored.endpoint == original.endpoint
    assert restored.extras == original.extras


def test_bridge_target_roundtrip_without_extras():
    original = BridgeTarget(protocol="a2a", endpoint="https://a2a.example.com")
    restored = BridgeTarget.from_dict(original.to_dict())
    assert restored.protocol == original.protocol
    assert restored.endpoint == original.endpoint
    assert restored.extras is None


# ── AnnounceFrame node_kind alias ─────────────────────────────────────────────

def _make_base_announce_dict(**overrides):
    """Return a minimal valid AnnounceFrame dict."""
    d = {
        "nid": "urn:nps:node:example.com:data",
        "addresses": [{"host": "example.com", "port": 17433, "protocol": "nwp"}],
        "capabilities": ["nwp/query"],
        "ttl": 300,
        "timestamp": "2026-01-01T00:00:00Z",
        "signature": "ed25519:fake",
    }
    d.update(overrides)
    return d


def test_announce_frame_node_kind_alias_same_as_node_roles():
    """node_kind key must be parsed as node_roles."""
    roles = ["memory", "bridge"]
    with_node_roles = AnnounceFrame.from_dict(
        _make_base_announce_dict(node_roles=roles)
    )
    with_node_kind = AnnounceFrame.from_dict(
        _make_base_announce_dict(node_kind=roles)
    )
    assert with_node_kind.node_roles == with_node_roles.node_roles
    assert with_node_kind.node_roles == tuple(roles)


def test_announce_frame_node_roles_takes_precedence_over_node_kind():
    """If both node_roles and node_kind are present, node_roles wins."""
    frame = AnnounceFrame.from_dict(
        _make_base_announce_dict(node_roles=["memory"], node_kind=["bridge"])
    )
    assert frame.node_roles == ("memory",)
